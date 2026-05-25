# Django Redis Task Lock

`django_redis_task_lock` provides a small locking helper for Django code that uses
Redis-backed Django cache locks. It supports both decorator and context-manager
usage and is used heavily around Celery task execution in this repository.

## Behavior

`lock(...)` supports two calling styles:

1. Decorator usage: `@lock(...)`
2. Context-manager usage: `with lock(function, args=[...], kwargs={...}, ...)`

In both cases, the helper:

- builds a lock name
- acquires a Redis lock from the configured Django cache
- returns the configured `locked` value when acquisition fails
- optionally releases the lock on completion if `release_on_completion=True`

By default, lock acquisition is non-blocking.

## Options

The following options are supported in both decorator and context-manager usage.

| Option name | Type | Default | Description |
| --- | --- | --- | --- |
| `lock_name` | `str | list[str | list | PriorityList]` | auto-generated | Lock key to acquire. See details below. |
| `timeout` | `int | float` | `60` | Lock TTL in seconds. |
| `blocking` | `bool` | `False` | Whether lock acquisition should block. |
| `blocking_timeout` | `int | float` | Redis default | Maximum time to wait when `blocking=True`. |
| `sleep` | `int | float` | Redis default | Sleep interval between blocking retries. |
| `cache` | `str` | `"default"` | Django cache alias used to create the lock. |
| `debug` | `bool` | `False` | Emit a debug log when lock acquisition fails. |
| `locked` | `Any` | `None` | Value returned or yielded when the lock is already held. |
| `release_on_completion` | `bool` | `False` | Explicitly release the lock in `finally`. |

## Lock Name Generation

If `lock_name` is not provided, the helper builds a key that starts with the
function name and appends all usable positional and keyword argument values:

```python
@lock
def foo(a, b=None):
    pass


foo(1, b=2)  # lock name => "foo:1:2"
```

Values that only render as the default Python object string or repr, such as
`<MyObject object at 0x...>`, are skipped to avoid unstable lock names.

## `lock_name` Forms

### String

Passing a string uses that value directly as the full Redis lock key.

```python
@lock(lock_name="nightly-report")
def run_report():
    pass
```

### List of Parameters

Passing a list lets you choose which function inputs become part of the key.
Each element may be:

- a parameter name as `str`
- an attribute/index traversal as `list`
- a `PriorityList`

```python
@lock(lock_name=["entity_id", "workflow_id"])
def process_entity(workflow_id, entity_id):
    pass


process_entity(10, 22)  # lock name => "process_entity:22:10"
```

The order in `lock_name` is the order used in the generated key.

### Attribute or Index Traversal

Use a nested list when the key should come from a value inside a parameter.
The first element must be the parameter name. Each later element is resolved
recursively as either an attribute access or an index/key lookup.

```python
class Example:
    def __init__(self):
        self.metadata = {"ids": [17]}

    @lock(lock_name=[["self", "metadata", "ids", 0], "task_type"])
    def run(self, task_type):
        pass
```

For `Example().run("sync")`, the lock name becomes:

```text
run:17:sync
```

### `PriorityList`

`PriorityList` selects the first configured parameter whose value is truthy.
This is useful when several identifiers may be available but only one should be
part of the lock key.

```python
from django_redis_task_lock import PriorityList, lock


@lock(lock_name=[PriorityList(["customer_id", "account_id", "email"])])
def sync_customer(customer_id=None, account_id=None, email=None):
    pass
```

If `customer_id` is falsey and `account_id=12`, the lock name becomes:

```text
sync_customer:12
```

If every value in the `PriorityList` is falsey, nothing is appended for that
element and the key remains whatever has already been built.

## Decorator Usage

```python
from django.conf import settings

from django_redis_task_lock import lock


@lock(
    lock_name=["content_type_id", "object_pk"],
    timeout=120,
    debug=settings.DEBUG,
    locked=settings.TASK_LOCK_MESSAGE,
)
def update_audit_snapshot(content_type_id, object_pk):
    return "done"
```

Behavior:

- if the lock is acquired, the wrapped function runs normally
- if the lock is already held, the decorator returns the `locked` value
- if `release_on_completion=True`, `lock.release()` is called in `finally`

## Context-Manager Usage

Use the context-manager form when you need a lock around only part of a
function, or when the protected code is not naturally expressed as a decorated
function.

```python
from django.conf import settings

from django_redis_task_lock import lock


def bulk_update_from_document_task(instance_id, content_type_id, batch_size=100, starting_idx=0):
    with lock(
        bulk_update_from_document_task,
        args=[instance_id, content_type_id],
        kwargs={
            "batch_size": batch_size,
            "starting_idx": starting_idx,
        },
        debug=settings.DEBUG,
        locked=settings.TASK_LOCK_MESSAGE,
        timeout=2400,
        lock_name=["instance_id", "content_type_id", "batch_size", "starting_idx"],
    ) as task_lock:
        if task_lock is not None:
            return settings.TASK_LOCK_MESSAGE

        return "work completed"
```

### Context-Manager Rules

- `func` is required and should be the function whose name/signature should be
  used to build the lock key.
- `args` should be a positional argument list or tuple. `None` is treated as an
  empty list.
- `kwargs` should be a dict of keyword arguments relevant to lock-name
  generation. `None` is treated as an empty dict.
- top-level lock options such as `timeout`, `cache`, `blocking`,
  `blocking_timeout`, `sleep`, and `release_on_completion` are honored
  directly.
- if the same option is present both at the top level and inside `kwargs`,
  the value inside `kwargs` wins. This preserves the library's historical
  calling pattern.

When acquisition fails, the context manager yields the configured `locked`
value. When acquisition succeeds, it yields `None`.

## Errors

The helper raises configuration errors when `lock_name` refers to values that do
not exist in the target function call or definition.

- `ValueError`: parameter name in `lock_name` is invalid or unavailable
- `KeyError`: nested attribute/key traversal fails
- `TypeError`: unsupported element type inside `lock_name`

## Practical Notes

- Prefer stable business identifiers in `lock_name` instead of full objects.
- Prefer explicit `lock_name` definitions for Celery tasks and other retryable
  workflows so the lock key stays predictable across releases.
- Use `release_on_completion=True` only when the code should release the lock
  immediately instead of letting the Redis timeout expire naturally.
