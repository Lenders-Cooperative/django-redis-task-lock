# Django Redis Task Lock
This is a redis task lock decorator for django celery tasks. There are four options
that can be passed to the decorator in any order as kwargs to modify the properties of the lock.

| Option name | Type | Default value | Description |
| --- | --- | --- | --- |
| lock_name | str / list[str, list, PriorityList] | *See note below* | The name of the redis lock |
| timeout | int | 60 | The timeout of the lock |
| blocking | bool | False | Set whether the lock is blocking or not |
| cache | str | 'default' | The Django cache to lock |
| debug | bool | False | Toggle debug output |

Note: If no value for lock_name is passed, the lock name will be auto-generated.
The generated name is based on all args and kwargs in the order they are passed
when the function is called and will follow the format `<function_name>:<args>:<kwargs>`.
For a function call such as `foo(1, 'temp', bar9=4, bar8=3)`, the lock_name would be `foo:1:temp:4:3`.
If a parameter is a type with only the default `__str__` or `__repr__`, it will not be included in the name.

## `lock_name` Explanation
lock_name is the option with the most flexibility depending on user needs. 
This option allows the user to either hard-code the lock name or use a subset of available parameters.

### Hard-coded Lock name
To hard-code the lock name, pass a string with the desired name.

### Variable Lock Name
By default, the name generator will use all valid args & kwargs.
Passing a list allows the user to instead select a subset of available parameters.
Elements of this list must either be strings, lists, or Priority Lists.

#### Strings
A string element indicates a *function parameter* should be part of the lock name.

#### Lists
A list element indicates an *attribute or element of a function parameter* an arbitrary number of layers deep 
should be part of the lock name.
This functionality is best explained in broad terms with some examples.

```python
class ClassExample:
    def __init__(self):
        self.id = 27
    
    @lock(lock_name=['arg1', ['self', 'id']])
    def foo(self, arg1):
        pass

@lock(lock_name=['arg4', 'arg2', 'arg3'])
def bar(arg1, arg2, arg3, arg4):
    pass
```

Take the above code as two example definitions.
In the case of `bar`, a function call of `bar(1, 2, 3, 4)` would get a lock name of `bar:4:2:3`
In the case of `foo`, a function call of `ClassExample().foo(1)` would get a lock name of `foo:1:27`.

Using a list to specify an element of the lock name is very flexible.
The first element should be a string specifing a function parameter.
Each additional element of the list should specify either an attribute or an index of the previous element.
The last element's string representation is used as the lock_name element.
To give an example that showcases everything possible, `["self", "obj1", "dict1", "list1", 0]` would 
look for `self.obj1.dict1["list1"][0]` when generating the lock name.

#### Priority Lists
A Priority List indicates *the first function parameter in the priority list that evaluates to True* should be part of the lock name.
`PriorityList` is a custom class included in the package.
It is functionally identical to python's built-in list type, but serves to mark a list for special evaluation when generating the lock name.
When a Priority List is being evaluated, the first parameter found with a value equivalent to True is used in the lock name.
The simplest way to use a priority list is to create an instance when defining the lock name.

Example: `@lock(lock_name=[ PriorityList(["foo", "bar"]) ])`

In the above example, the lock name would first try to be `<func_name>:<foo>`.
If the value of `foo` would evaluate to False, the lock name would then try to be `<func_name>:<bar>`.
If `bar` also evaluated to False, the lock name would be `<func_name>`.

A normal list can not be an element of a priority list.

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

@lock(lock_name=["kwarg", "arg"])
def kwarg(arg, kwarg):
    # lock_name would be kwarg:<kwarg>:<arg>
    pass

@lock(lock_name=[ PriorityList(["kwarg1", "kwarg2", "arg"]) ])
def priority_list(arg=None, kwarg1=None, kwarg2=None):
    # For a function call of priority_list(0, kwarg2=2), lock_name would be priority_list:2

@lock(timeout=20)
def timeout(arg, kwarg):
    pass

@lock(blocking=True)
def blocking(arg, kwarg):
    pass

@lock(debug=True)
def debug(arg, kwarg):
    pass

@lock(lock_name=[ ["self", "name_list", 1], "debug", PriorityList(["is_resend", "language"]) ], timeout=30, cache='other', blocking=True, debug=True)
def combination(self, url=None, language='en', is_resend=False, debug=False):
    # lock_name would be combination:<self.name_list[1]>:<debug>: <is_resend>/<language>/''
    # timeout would be 30 sec instead of 60 sec
    # lock would attempt to use a Django cache named 'other'
    # lock would be blocking
    # decorator would print debug information
    pass

```