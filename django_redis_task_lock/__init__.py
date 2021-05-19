from re import match
from functools import wraps
from django.core.cache import caches

def lock(*args, **options):
    def _lock(func):
        @wraps(func)
        def __wrapper(*args, **kwargs):
            default_str_regex = "<.*object at.*>"
            default_repr_regex = default_str_regex
            lock_name = f'{func.__name__}'

            # If passing lock_name as decorator option, it can be either a string or a list of the variables to
            # use as part of the name. If list is used, specified variables must be kwargs, or instance attribute
            if "lock_name" in options:
                if type(options["lock_name"]) is str:
                    lock_name = options["lock_name"]
                elif type(options["lock_name"]) is list:
                    for var in options["lock_name"]:
                        if var in kwargs:
                            lock_name += ":" + str(kwargs[var])
                        else:
                            try:
                                lock_name += ":" + str(getattr(args[0], var))
                            except AttributeError as e:
                                message = str(e)+"\nThe lock decorator could not find a kwarg or an instance attribute matching a specified kwarg."
                                raise AttributeError(message) from None
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

            lock = caches[options.get('cache','default')].lock(lock_name, timeout=options.get('timeout',60))
            if not lock.acquire(options.get('blocking', False)):
                return

            return func(*args, **kwargs)
        return __wrapper

    if len(args) == 1 and callable(args[0]):
        return _lock(args[0])
    return _lock