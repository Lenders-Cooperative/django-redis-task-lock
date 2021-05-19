# Django Redis Task Lock
This is a redis task lock decorator for django celery tasks. There are four options
that can be passed to the decorator in any order as kwargs to modify the properties of the lock.

| Option name | Type | Default value | Description |
| --- | --- | --- | --- |
| lock_name | str / list[str] | *See note below* | The name of the redis lock |
| timeout | int | 60 | The timeout of the lock |
| blocking | bool | False | Set whether the lock is blocking or not |
| cache | str | 'default' | The Django cache to lock |

Note: If no value for lock_name is passed, the lock name will be auto-generated.
The generated name is based on all args and kwargs in the order they are passed
when the function is called and will follow the format `<function_name>:<args>:<kwargs>`.
For a function call such as `foo(1, 'temp', bar9=4, bar8=3)`, the lock_name would be `foo:1:temp:4:3`.
If a parameter is a type with only the default `__str__` or `__repr__`, it will not be included in the name.

## `lock_name` Explanation
lock_name is the option with the most flexibility depending on user needs. 
This option allows the user to either hard-code the lock name or use a subset of available parameters.

To hard-code the lock name, pass a string with the desired name.

By default, the name generator will use all valid args & kwargs.
Passing a list of strings allows the user to instead select a subset of kwargs or instance attributes.
Currently, args cannot be selected to be included in lock_name.
This functionality is best explained with an example.

```python
class ClassExample:
    def __init__(self):
        self.id = 27
    
    @lock(lock_name=['arg1', 'id'])
    def foo(self, arg1):
        pass

@lock(lock_name=['arg4', 'arg2', 'arg3'])
def bar(arg1, arg2, arg3, arg4):
    pass
```

Take the above code as two example definitions.
In the case of `bar`, a function call of `bar(1, arg2=2, arg3=3, arg4=4)` would get a lock name of `bar:4:2:3`

If a specified string is not found in kwargs, the decorator will assume it's decorating a class function and
and the user wants to use an instance attribute instead of a kwarg.
For example, a function call of `ClassExample().foo(1)` would get a lock name of `foo:1:27`.
To be specific, if a string is not found in kwargs, the decorator will then check if it's an attribute of args[0],
which in the context of an instance function corresponds to `self`.
This means that it is not possible for an attribute of a non-self parameter to be part of the lock_name. 

## Examples
```python
@lock
def plain(arg, kwarg):
    # lock_name would be plain:<arg>:<kwarg>
    pass

@lock(lock_name="hard_code_name")
def hard_name(arg, kwarg):
    # lock_name would be hard_name:hard_code_name
    pass

@lock(lock_name=["kwarg"])
def kwarg(arg, kwarg):
    # lock_name would be kwarg:<kwarg>
    pass

@lock(timeout=20)
def timeout(arg, kwarg):
    pass

@lock(blocking=True)
def blocking(arg, kwarg):
    pass

@lock(lock_name=["id", "debug"], timeout=30, cache='other', blocking=True)
def combination(self, preview_return_url=None, language_code='en', is_resend=False, debug=False):
    # lock_name would be combination:<self.id>:<debug>
    # timeout would be 30 sec instead of 60 sec
    # lock would attempt to use a Django cache named 'other'
    # lock would be blocking
    pass

```