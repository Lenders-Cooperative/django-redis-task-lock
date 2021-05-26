from re import match
from functools import wraps
from inspect import getfullargspec
from django.core.cache import caches

def __attr_finder(obj, attr_list):
    attr = attr_list[0]
    if type(attr) is str and hasattr(obj, attr):
        attr_val = getattr(obj, attr)
    else:
        try:
            attr_val = obj[attr]
        except KeyError as e:
            message = str(e)+f'\nThe lock decorator is configured incorrectly. Could not find attribute/key "{attr}" of object "{obj}"'
            raise KeyError(message) from None
    if len(attr_list) == 1:
        return str(attr_val)
    else:
        return __attr_finder(attr_val, attr_list[1:])


def lock(*args, **options):
    def _lock(func):
        @wraps(func)
        def __wrapper(*args, **kwargs):
            default_str_regex = "<.*object at.*>"
            default_repr_regex = default_str_regex
            lock_name = f'{func.__name__}'

            if "lock_name" in options:
                if type(options["lock_name"]) is str:
                    lock_name = options["lock_name"]
                elif type(options["lock_name"]) is list:
                    for var in options["lock_name"]:
                        if type(var) is str:
                            if var in kwargs:
                                lock_name += ":" + str(kwargs[var])
                            elif var in getfullargspec(func)[0]:
                                arg_index = getfullargspec(func)[0].index(var)
                                lock_name += ":" + str(args[arg_index])
                            else:
                                message = f'\nThe lock decorator is configured incorrectly. Could not find parameter "{var}" in function call'
                                raise ValueError(message)
                        elif type(var) is list:
                            param_name = var[0]
                            if param_name in kwargs:
                                lock_name += ":" + __attr_finder(kwargs[param_name], var[1:])
                            elif param_name in getfullargspec(func)[0]:
                                arg_index = getfullargspec(func)[0].index(param_name)
                                lock_name += ":" + __attr_finder(args[arg_index], var[1:])
                            else:
                                message = f'\nThe lock decorator is configured incorrectly. Could not find parameter "{param_name}" in function call'
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

            lock = caches[options.get('cache','default')].lock(lock_name, timeout=options.get('timeout',60))
            if not lock.acquire(options.get('blocking', False)):
                if options.get('debug', False):
                    print(f'{lock_name} lock already acquired...return')
                return

            return func(*args, **kwargs)
        return __wrapper

    if len(args) == 1 and callable(args[0]):
        return _lock(args[0])
    return _lock