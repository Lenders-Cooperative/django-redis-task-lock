from contextlib import contextmanager
from functools import wraps
from inspect import getfullargspec
from re import match

from django.core.cache import caches


class PriorityList(list):
    pass


def __attr_finder(obj, attr_list):
    attr = attr_list[0]
    if type(attr) is str and hasattr(obj, attr):
        attr_val = getattr(obj, attr)
    else:
        try:
            attr_val = obj[attr]
        except KeyError as e:
            message = (
                str(e)
                + f'\nThe lock decorator is configured incorrectly. Could not find attribute/key "{attr}" of object "{obj}"'
            )
            raise KeyError(message) from None
    if len(attr_list) == 1:
        return str(attr_val)
    else:
        return __attr_finder(attr_val, attr_list[1:])


def lock(*args, **options):
    def _lock(func):
        @wraps(func)
        def __wrapper(*args, **kwargs):
            lock_name = construct_lock_name(func, args, options, **kwargs)
            lock = acquire_lock(lock_name, options)
            if lock is None:
                return options.get("locked", None)
            try:
                return func(*args, **kwargs)
            finally:
                # Can optionally release the lock here
                pass

        return __wrapper

    @contextmanager
    def _lock_context(func, *args, **kwargs):
        options = {
            "debug": kwargs.pop("debug", False),
            "cache": kwargs.pop("cache", "default"),
            "locked": kwargs.pop("locked", None),
            "blocking": kwargs.pop("blocking", False),
            "timeout": kwargs.pop("timeout", 60),
            "lock_name": kwargs.pop("lock_name", []),
        }
        kwargs["options"] = options
        lock_name = construct_lock_name(func, args, **kwargs)
        lock = acquire_lock(lock_name, options)
        if lock is None:
            yield options.get("locked", None)
        else:
            try:
                yield
            finally:
                # Can optionally release the lock here
                pass

    if len(args) == 1 and callable(args[0]):
        return _lock_context(args[0], **options)
    return _lock


def construct_lock_name(func, args, options, **kwargs):
    default_str_regex = "<.*object at.*>"
    default_repr_regex = default_str_regex
    lock_name = f"{func.__name__}"

    if "lock_name" in options:
        if type(options["lock_name"]) is str:
            lock_name = options["lock_name"]
        elif type(options["lock_name"]) is list:
            arg_spec = getfullargspec(func)
            param_list = arg_spec[0]
            defaults = arg_spec[3]
            for var in options["lock_name"]:
                if type(var) is str:
                    if var in kwargs:
                        lock_name += ":" + str(kwargs[var])
                    elif var in param_list:
                        arg_index = param_list.index(var)
                        if len(args) > arg_index:
                            lock_name += ":" + str(args[arg_index])
                        else:
                            default_index = param_list[-len(defaults) :].index(var)
                            lock_name += ":" + str(defaults[default_index])
                    else:
                        message = f'\nThe lock decorator is configured incorrectly. Could not find parameter "{var}" in function call or definition'
                        raise ValueError(message)
                elif type(var) is list:
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
                                default_index = param_list[-len(defaults) :].index(param_name)
                                if defaults[default_index]:
                                    lock_name += ":" + str(defaults[default_index])
                                    break
                        else:
                            message = f'\nThe lock decorator is configured incorrectly. Could not find parameter "{param_name}" within PriorityList {var} in function call or definition'
                            raise ValueError(message)
                elif type(var) is list:
                    param_name = var[0]
                    if param_name in kwargs:
                        lock_name += ":" + __attr_finder(kwargs[param_name], var[1:])
                    elif param_name in param_list:
                        arg_index = param_list.index(param_name)
                        if len(args) > arg_index:
                            lock_name += ":" + __attr_finder(args[arg_index], var[1:])
                        else:
                            default_index = param_list[-len(defaults) :].index(param_name)
                            lock_name += ":" + __attr_finder(defaults[default_index], var[1:])
                    else:
                        message = f'\nThe lock decorator is configured incorrectly. Could not find parameter "{param_name}" in function call or definition'
                        raise ValueError(message)
                else:
                    message = f'\nThe lock decorator is configured incorrectly. "{type(var)}" is not a valid type for specifying a lock name'
                    raise TypeError(message)

    else:
        if args:
            for arg in args:
                if not match(default_str_regex, str(arg)):
                    lock_name += ":" + str(arg)
                elif not match(default_repr_regex, repr(arg)):
                    lock_name += ":" + repr(arg)
        if kwargs:
            for val in kwargs.values():
                if not match(default_str_regex, str(val)):
                    lock_name += ":" + str(val)
                elif not match(default_repr_regex, repr(val)):
                    lock_name += ":" + repr(val)

    return lock_name


def acquire_lock(lock_name, options):
    # Code block where the lock is acquired
    lock = caches[options.get("cache", "default")].lock(lock_name, timeout=options.get("timeout", 60))
    # Checks if the lock is already acquired
    if not lock.acquire(options.get("blocking", False)):
        if options.get("debug", False):
            print(f"{lock_name} lock already acquired...return")
        return None
    return lock
