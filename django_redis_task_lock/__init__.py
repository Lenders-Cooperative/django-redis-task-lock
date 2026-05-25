"""Redis-backed task locking helpers used by shared Django applications."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from functools import wraps
from inspect import getfullargspec
from re import match
from typing import (
    Any,
    ContextManager,
    Final,
    ParamSpec,
    Protocol,
    TypeAlias,
    TypedDict,
    TypeVar,
    cast,
)

import structlog
from django.core.cache import caches

LOGGER = structlog.get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

LockNameAttribute: TypeAlias = str | int


class LockProtocol(Protocol):
    """Protocol describing the Redis lock methods used by this module."""

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        """Attempt to acquire the lock."""

    def release(self) -> None:
        """Release the lock."""


class CacheProtocol(Protocol):
    """Protocol describing the cache API used to create a lock."""

    def lock(self, lock_name: str, timeout: int | float) -> LockProtocol:
        """Create a lock for the given cache key."""


class PriorityList(list[str]):
    """Parameter names evaluated in order to build a lock-name component."""


LockNamePart: TypeAlias = str | PriorityList | list[LockNameAttribute]
LockNameOption: TypeAlias = str | list[LockNamePart]


class LockOptions(TypedDict, total=False):
    """Options supported by the lock decorator and context-manager helper."""

    debug: bool
    cache: str
    locked: Any
    blocking: bool
    timeout: int | float
    lock_name: LockNameOption
    release_on_completion: bool
    blocking_timeout: int | float
    sleep: int | float


LOCK_OPTION_KEYS: Final[set[str]] = {
    "debug",
    "cache",
    "locked",
    "blocking",
    "timeout",
    "lock_name",
    "release_on_completion",
    "blocking_timeout",
    "sleep",
}


def __attr_finder(obj: Any, attr_list: Sequence[LockNameAttribute]) -> str:
    """Resolve an attribute or mapping/index chain into a string lock component.

    Args:
        obj: Object, mapping, or sequence that contains the desired value.
        attr_list: Ordered attribute names or mapping/index keys to traverse.

    Returns:
        The resolved value converted to a string.

    Raises:
        KeyError: If a requested key cannot be found while traversing the chain.
    """
    attr = attr_list[0]
    if type(attr) is str and hasattr(obj, attr):
        attr_val = getattr(obj, attr)
    else:
        try:
            attr_val = obj[attr]
        except KeyError as exc:
            message = (
                str(exc)
                + f'\nThe lock decorator is configured incorrectly. Could not find attribute/key "{attr}" of object "{obj}"'
            )
            raise KeyError(message) from None

    if len(attr_list) == 1:
        return str(attr_val)

    return __attr_finder(attr_val, attr_list[1:])


def _build_context_options(
    base_options: LockOptions, call_kwargs: MutableMapping[str, Any]
) -> LockOptions:
    """Build context-manager lock options from explicit and keyword overrides.

    Args:
        base_options: Lock options passed directly to ``lock(...)``.
        call_kwargs: Keyword arguments associated with the protected function
            call. Matching lock-option keys are removed and treated as overrides.

    Returns:
        A merged options dictionary containing defaults, explicit options, and
        compatible overrides extracted from ``call_kwargs``.
    """
    options: LockOptions = {
        "debug": False,
        "cache": "default",
        "locked": None,
        "blocking": False,
        "timeout": 60,
        "lock_name": [],
        "release_on_completion": False,
    }
    options.update(base_options)

    for option_name in LOCK_OPTION_KEYS:
        if option_name in call_kwargs:
            options[option_name] = call_kwargs.pop(option_name)

    return options


def lock(
    *args: Any,
    **options: Any,
) -> Callable[[Callable[P, R]], Callable[P, R | Any]] | ContextManager[Any | None]:
    """Create a lock-aware decorator or context manager.

    This helper supports two calling styles:

    - ``@lock(...)`` returns a decorator for a callable.
    - ``with lock(func, args=[...], kwargs={...}, ...)`` returns a context
      manager that acquires a lock for part of a workflow.

    Args:
        *args: Either the callable for context-manager usage or no positional
            arguments for decorator usage.
        **options: Lock acquisition and lock-name configuration options.

    Returns:
        Either a decorator that wraps a callable with lock acquisition, or a
        context manager that yields the configured ``locked`` value when the
        lock cannot be acquired.
    """

    def _lock(func: Callable[P, R]) -> Callable[P, R | Any]:
        """Wrap a callable so it runs only when the Redis lock is acquired.

        Args:
            func: Function to guard with a Redis-backed lock.

        Returns:
            A wrapped callable that either executes ``func`` or returns the
            configured ``locked`` fallback value.
        """

        @wraps(func)
        def __wrapper(*args: P.args, **kwargs: P.kwargs) -> R | Any:
            lock_name = construct_lock_name(func, args, kwargs, **options)
            redis_lock = acquire_lock(lock_name, options)
            if redis_lock is None:
                return options.get("locked", None)
            try:
                return func(*args, **kwargs)
            finally:
                if options.get("release_on_completion", False) is True:
                    try:
                        redis_lock.release()
                    except Exception:
                        pass

        return __wrapper

    @contextmanager
    def _lock_context(
        func: Callable[..., Any],
        args: Sequence[Any] | None,
        kwargs: Mapping[str, Any] | None,
        **options: Any,
    ) -> Iterator[Any | None]:
        """Acquire a lock for a manually managed block of work.

        Args:
            func: Callable whose name and signature should be used when building
                the lock key.
            args: Positional arguments that should participate in lock-name
                generation.
            kwargs: Keyword arguments that should participate in lock-name
                generation.
            **options: Lock acquisition and lock-name configuration options.

        Yields:
            The configured ``locked`` fallback value when the lock cannot be
            acquired. Otherwise yields ``None`` after the lock is acquired.
        """
        positional_args = [] if args is None else list(args)
        keyword_args: dict[str, Any] = {} if kwargs is None else dict(kwargs)
        context_options = _build_context_options(options, keyword_args)
        keyword_args["options"] = context_options
        lock_name = construct_lock_name(
            func, positional_args, keyword_args, **context_options
        )
        redis_lock = acquire_lock(lock_name, context_options)
        if redis_lock is None:
            yield context_options.get("locked", None)
        else:
            try:
                yield None
            finally:
                if context_options["release_on_completion"] is True:
                    try:
                        redis_lock.release()
                    except Exception:
                        pass

    if len(args) == 1 and callable(args[0]):
        return _lock_context(args[0], **options)
    return _lock


def construct_lock_name(
    func: Callable[..., Any],
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    **options: Any,
) -> str:
    """Construct the Redis lock name for a function invocation.

    Args:
        func: Function whose invocation is being locked.
        args: Positional arguments passed to the function.
        kwargs: Keyword arguments passed to the function.
        **options: Lock configuration, including the optional ``lock_name``
            override.

    Returns:
        A Redis lock name derived from the configured rules and call inputs.

    Raises:
        ValueError: If a configured parameter name cannot be resolved from the
            function call or signature.
        TypeError: If an unsupported type is used inside ``lock_name``.
    """
    default_str_regex = "<.*object at.*>"
    default_repr_regex = default_str_regex
    lock_name = f"{func.__name__}"

    if "lock_name" in options:
        lock_name_option = options["lock_name"]
        if isinstance(lock_name_option, str):
            lock_name = lock_name_option
        elif isinstance(lock_name_option, list):
            arg_spec = getfullargspec(func)
            param_list = arg_spec.args
            defaults = arg_spec.defaults or ()
            default_param_list = param_list[-len(defaults) :] if defaults else []
            for var in lock_name_option:
                if isinstance(var, str):
                    if var in kwargs:
                        lock_name += ":" + str(kwargs[var])
                    elif var in param_list:
                        arg_index = param_list.index(var)
                        if len(args) > arg_index:
                            lock_name += ":" + str(args[arg_index])
                        else:
                            if var not in default_param_list:
                                message = (
                                    "\nThe lock decorator is configured incorrectly. Could not find parameter "
                                    f'"{var}" in function call or definition'
                                )
                                raise ValueError(message)
                            default_index = default_param_list.index(var)
                            lock_name += ":" + str(defaults[default_index])
                    else:
                        message = (
                            "\nThe lock decorator is configured incorrectly. Could not find parameter "
                            f'"{var}" in function call or definition'
                        )
                        raise ValueError(message)
                elif isinstance(var, PriorityList):
                    for param_name in var:
                        if param_name in kwargs:
                            if kwargs[param_name]:
                                lock_name += ":" + str(kwargs[param_name])
                                break
                        elif param_name in param_list:
                            arg_index = param_list.index(param_name)
                            if len(args) > arg_index:
                                if args[arg_index]:
                                    lock_name += ":" + str(args[arg_index])
                                    break
                            else:
                                if param_name not in default_param_list:
                                    message = (
                                        "\nThe lock decorator is configured incorrectly. Could not find "
                                        f'parameter "{param_name}" within PriorityList {var} in function call or definition'
                                    )
                                    raise ValueError(message)
                                default_index = default_param_list.index(param_name)
                                if defaults[default_index]:
                                    lock_name += ":" + str(defaults[default_index])
                                    break
                        else:
                            message = (
                                "\nThe lock decorator is configured incorrectly. Could not find "
                                f'parameter "{param_name}" within PriorityList {var} in function call or definition'
                            )
                            raise ValueError(message)
                elif isinstance(var, list):
                    param_name = var[0]
                    if param_name in kwargs:
                        lock_name += ":" + __attr_finder(kwargs[param_name], var[1:])
                    elif param_name in param_list:
                        arg_index = param_list.index(param_name)
                        if len(args) > arg_index:
                            lock_name += ":" + __attr_finder(args[arg_index], var[1:])
                        else:
                            if param_name not in default_param_list:
                                message = (
                                    "\nThe lock decorator is configured incorrectly. Could not find parameter "
                                    f'"{param_name}" in function call or definition'
                                )
                                raise ValueError(message)
                            default_index = default_param_list.index(param_name)
                            lock_name += ":" + __attr_finder(
                                defaults[default_index], var[1:]
                            )
                    else:
                        message = (
                            "\nThe lock decorator is configured incorrectly. Could not find parameter "
                            f'"{param_name}" in function call or definition'
                        )
                        raise ValueError(message)
                else:
                    message = (
                        '\nThe lock decorator is configured incorrectly. "'
                        f'{type(var)}" is not a valid type for specifying a lock name'
                    )
                    raise TypeError(message)
    else:
        for arg in args:
            if not match(default_str_regex, str(arg)):
                lock_name += ":" + str(arg)
            elif not match(default_repr_regex, repr(arg)):
                lock_name += ":" + repr(arg)
        for val in kwargs.values():
            if not match(default_str_regex, str(val)):
                lock_name += ":" + str(val)
            elif not match(default_repr_regex, repr(val)):
                lock_name += ":" + repr(val)

    return lock_name


def acquire_lock(lock_name: str, options: Mapping[str, Any]) -> LockProtocol | None:
    """Acquire a Redis lock using the configured cache and acquire options.

    Args:
        lock_name: Name of the Redis lock to acquire.
        options: Lock acquisition settings such as cache alias, timeout, and
            blocking behavior.

    Returns:
        The acquired lock object when acquisition succeeds. Returns ``None``
        when the lock is already held by another worker.
    """
    cache_backend = caches[options.get("cache", "default")]
    redis_lock = cast(
        LockProtocol, cache_backend.lock(lock_name, timeout=options.get("timeout", 60))
    )

    acquire_kwargs: dict[str, Any] = {"blocking": options.get("blocking", False)}
    if "blocking_timeout" in options:
        acquire_kwargs["blocking_timeout"] = options["blocking_timeout"]
    if "sleep" in options:
        acquire_kwargs["sleep"] = options["sleep"]

    if not redis_lock.acquire(**acquire_kwargs):
        if options.get("debug", False):
            LOGGER.debug("lock already acquired", lock_name=lock_name)
        return None

    return redis_lock
