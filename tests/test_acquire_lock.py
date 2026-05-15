import unittest
import sys
import types

if "django.core.cache" not in sys.modules:
    django_module = types.ModuleType("django")
    core_module = types.ModuleType("django.core")
    cache_module = types.ModuleType("django.core.cache")
    cache_module.caches = {}
    django_module.core = core_module
    core_module.cache = cache_module
    sys.modules.setdefault("django", django_module)
    sys.modules.setdefault("django.core", core_module)
    sys.modules.setdefault("django.core.cache", cache_module)

import django_redis_task_lock


class FakeLock:
    def __init__(self, acquired):
        self.acquired = acquired
        self.acquire_args = None
        self.acquire_kwargs = None

    def acquire(self, *args, **kwargs):
        self.acquire_args = args
        self.acquire_kwargs = kwargs
        return self.acquired


class FakeCache:
    def __init__(self, lock):
        self.created_lock = lock
        self.lock_name = None
        self.lock_timeout = None

    def lock(self, lock_name, timeout):
        self.lock_name = lock_name
        self.lock_timeout = timeout
        return self.created_lock


class AcquireLockTests(unittest.TestCase):
    def setUp(self):
        self.original_caches = django_redis_task_lock.caches

    def tearDown(self):
        django_redis_task_lock.caches = self.original_caches

    def test_acquire_lock_passes_blocking_as_keyword(self):
        redis_lock = FakeLock(acquired=False)
        django_redis_task_lock.caches = {"default": FakeCache(redis_lock)}

        lock = django_redis_task_lock.acquire_lock(
            "task-lock",
            {"blocking": False, "timeout": 60},
        )

        self.assertIsNone(lock)
        self.assertEqual(redis_lock.acquire_args, ())
        self.assertEqual(redis_lock.acquire_kwargs, {"blocking": False})

    def test_acquire_lock_uses_configured_cache_and_timeout(self):
        redis_lock = FakeLock(acquired=True)
        cache = FakeCache(redis_lock)
        django_redis_task_lock.caches = {"tasks": cache}

        lock = django_redis_task_lock.acquire_lock(
            "task-lock",
            {"cache": "tasks", "blocking": True, "timeout": 120},
        )

        self.assertIs(lock, redis_lock)
        self.assertEqual(cache.lock_name, "task-lock")
        self.assertEqual(cache.lock_timeout, 120)
        self.assertEqual(redis_lock.acquire_kwargs, {"blocking": True})

    def test_acquire_lock_passes_modern_redis_lock_options(self):
        redis_lock = FakeLock(acquired=True)
        django_redis_task_lock.caches = {"default": FakeCache(redis_lock)}

        django_redis_task_lock.acquire_lock(
            "task-lock",
            {"blocking": True, "blocking_timeout": 5, "sleep": 0.1},
        )

        self.assertEqual(
            redis_lock.acquire_kwargs,
            {"blocking": True, "blocking_timeout": 5, "sleep": 0.1},
        )


if __name__ == "__main__":
    unittest.main()
