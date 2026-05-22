"""Focused tests for the shared Redis task lock helper."""

from __future__ import annotations

from typing import Any

from pytest import MonkeyPatch

import django_redis_task_lock


class FakeLock:
    """Simple lock double that records acquire and release behavior."""

    acquired: bool
    acquire_args: tuple[Any, ...] | None
    acquire_kwargs: dict[str, Any] | None
    released: bool

    def __init__(self, acquired: bool) -> None:
        """Initialize the fake lock.

        Args:
            acquired: Whether ``acquire`` should report success.
        """
        self.acquired = acquired
        self.acquire_args = None
        self.acquire_kwargs = None
        self.released = False

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        """Record the acquire call and return the configured result."""
        self.acquire_args = args
        self.acquire_kwargs = kwargs
        return self.acquired

    def release(self) -> None:
        """Record that the lock was released."""
        self.released = True


class FakeCache:
    """Cache double that returns a preconfigured lock object."""

    created_lock: FakeLock
    lock_name: str | None
    lock_timeout: int | float | None

    def __init__(self, lock: FakeLock) -> None:
        """Initialize the fake cache.

        Args:
            lock: Lock instance returned for every cache lock request.
        """
        self.created_lock = lock
        self.lock_name = None
        self.lock_timeout = None

    def lock(self, lock_name: str, timeout: int | float) -> FakeLock:
        """Record the requested lock details and return the configured lock."""
        self.lock_name = lock_name
        self.lock_timeout = timeout
        return self.created_lock


def test_acquire_lock_passes_blocking_as_keyword(monkeypatch: MonkeyPatch) -> None:
    redis_lock = FakeLock(acquired=False)
    monkeypatch.setattr(
        django_redis_task_lock,
        "caches",
        {"default": FakeCache(redis_lock)},
    )

    lock = django_redis_task_lock.acquire_lock(
        "task-lock",
        {"blocking": False, "timeout": 60},
    )

    assert lock is None
    assert redis_lock.acquire_args == ()
    assert redis_lock.acquire_kwargs == {"blocking": False}


def test_acquire_lock_uses_configured_cache_and_timeout(
    monkeypatch: MonkeyPatch,
) -> None:
    redis_lock = FakeLock(acquired=True)
    cache = FakeCache(redis_lock)
    monkeypatch.setattr(django_redis_task_lock, "caches", {"tasks": cache})

    lock = django_redis_task_lock.acquire_lock(
        "task-lock",
        {"cache": "tasks", "blocking": True, "timeout": 120},
    )

    assert lock is redis_lock
    assert cache.lock_name == "task-lock"
    assert cache.lock_timeout == 120
    assert redis_lock.acquire_kwargs == {"blocking": True}


def test_acquire_lock_passes_modern_redis_lock_options(
    monkeypatch: MonkeyPatch,
) -> None:
    redis_lock = FakeLock(acquired=True)
    monkeypatch.setattr(
        django_redis_task_lock,
        "caches",
        {"default": FakeCache(redis_lock)},
    )

    django_redis_task_lock.acquire_lock(
        "task-lock",
        {"blocking": True, "blocking_timeout": 5, "sleep": 0.1},
    )

    assert redis_lock.acquire_kwargs == {
        "blocking": True,
        "blocking_timeout": 5,
        "sleep": 0.1,
    }


def test_lock_context_uses_explicit_options(monkeypatch: MonkeyPatch) -> None:
    redis_lock = FakeLock(acquired=True)
    cache = FakeCache(redis_lock)
    monkeypatch.setattr(django_redis_task_lock, "caches", {"tasks": cache})

    def sample_task(instance_id: int) -> int:
        return instance_id

    with django_redis_task_lock.lock(
        sample_task,
        args=[42],
        kwargs={},
        cache="tasks",
        timeout=90,
        blocking=True,
        blocking_timeout=3,
        sleep=0.5,
        release_on_completion=True,
    ):
        pass

    assert cache.lock_name == "sample_task"
    assert cache.lock_timeout == 90
    assert redis_lock.acquire_kwargs == {
        "blocking": True,
        "blocking_timeout": 3,
        "sleep": 0.5,
    }
    assert redis_lock.released is True


def test_lock_context_allows_kwargs_to_override_base_options(
    monkeypatch: MonkeyPatch,
) -> None:
    redis_lock = FakeLock(acquired=True)
    cache = FakeCache(redis_lock)
    monkeypatch.setattr(django_redis_task_lock, "caches", {"tasks": cache})

    def sample_task(instance_id: int) -> int:
        return instance_id

    with django_redis_task_lock.lock(
        sample_task,
        args=[42],
        kwargs={"timeout": 15, "cache": "tasks", "blocking": True},
        timeout=90,
        cache="default",
        blocking=False,
    ):
        pass

    assert cache.lock_timeout == 15
    assert redis_lock.acquire_kwargs == {"blocking": True}
