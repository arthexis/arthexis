from __future__ import annotations

import datetime
import itertools
from typing import Callable, Iterable

from django.db import models
from django.utils import timezone


class CacheStore(models.Model):
    """Persisted cache entries with optional refresh windows."""

    key = models.CharField(max_length=255, unique=True)
    payload = models.JSONField(blank=True, null=True)
    refresh_interval = models.DurationField(blank=True, null=True)
    refreshed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "locals_cache_store"
        verbose_name = "Cache Store"
        verbose_name_plural = "Cache Stores"

    def should_refresh(self) -> bool:
        if self.refresh_interval in (None, datetime.timedelta(0)):
            return False
        if self.refreshed_at is None:
            return True
        return timezone.now() - self.refreshed_at >= self.refresh_interval

    def refresh(self, builder: Callable[[], object]) -> object:
        value = builder()
        self.payload = value
        self.refreshed_at = timezone.now()
        self.save(update_fields=["payload", "refreshed_at"])
        return value

    def invalidate(self) -> None:
        self.payload = None
        self.refreshed_at = timezone.now()
        self.save(update_fields=["payload", "refreshed_at"])

    def get_value(
        self, builder: Callable[[], object] | None, *, force_refresh: bool = False
    ) -> object:
        if force_refresh or self.should_refresh() or self.payload is None:
            if builder is None:
                return self.payload
            return self.refresh(builder)
        return self.payload


class CacheStoreMixin:
    """Mixin to back model caches with :class:`CacheStore` records."""

    cache_prefix: str = ""
    cache_refresh_interval: datetime.timedelta | None = None

    @classmethod
    def cache_key_for_identifier(cls, identifier: int | str) -> str:
        prefix = cls.cache_prefix or cls.__name__.lower()
        return f"{prefix}:{identifier}"

    @classmethod
    def _refresh_defaults(cls) -> dict[str, datetime.timedelta]:
        defaults: dict[str, datetime.timedelta] = {}
        if cls.cache_refresh_interval is not None:
            defaults["refresh_interval"] = cls.cache_refresh_interval
        return defaults

    @classmethod
    def get_cache_store(cls, identifier: int | str) -> CacheStore:
        cache_key = cls.cache_key_for_identifier(identifier)
        return get_cache_store(cache_key, refresh_interval=cls.cache_refresh_interval)

    @classmethod
    def get_cached_value(
        cls,
        identifier: int | str,
        builder: Callable[[], object],
        *,
        force_refresh: bool = False,
    ) -> object:
        store = cls.get_cache_store(identifier)
        return store.get_value(builder, force_refresh=force_refresh)

    @classmethod
    def invalidate_cached_value(cls, identifier: int | str) -> None:
        store = cls.get_cache_store(identifier)
        store.invalidate()


def cache_store(
    key: str,
    *,
    refresh_interval: datetime.timedelta | None = None,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Decorator to persist the result of a callable in a :class:`CacheStore`."""

    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        def wrapper(*args: Iterable[object], **kwargs: object) -> object:
            store = get_cache_store(key, refresh_interval=refresh_interval)
            return store.get_value(lambda: func(*args, **kwargs))

        return wrapper

    return decorator


def build_cache_key(*parts: object) -> str:
    """Join cache key parts using ``:`` separators."""

    return ":".join(str(part) for part in parts)


def cache_key_boolean_variants(
    base_key: str, **boolean_options: bool | None
) -> list[str]:
    """Return cache keys for boolean option combinations.

    Each boolean option produces ``0``/``1`` suffixes on the ``base_key``. A
    ``None`` value expands to both ``False`` and ``True`` combinations, which is
    useful for cache invalidation across variations.
    """

    variant_values = [
        (False, True) if value is None else (bool(value),)
        for value in boolean_options.values()
    ]

    if not variant_values:
        return [base_key]

    return [
        f"{base_key}:{':'.join(str(int(value)) for value in combination)}"
        for combination in itertools.product(*variant_values)
    ]


def get_cached_value_for_key(
    cache_key: str,
    builder: Callable[[], object],
    *,
    refresh_interval: datetime.timedelta | None = None,
    force_refresh: bool = False,
) -> object:
    """Return a cached value for ``cache_key`` backed by :class:`CacheStore`."""

    store = get_cache_store(cache_key, refresh_interval=refresh_interval)
    return store.get_value(builder, force_refresh=force_refresh)


def get_cache_store(
    key: str, *, refresh_interval: datetime.timedelta | None = None
) -> CacheStore:
    """Return a :class:`CacheStore`, ensuring refresh interval alignment."""

    defaults = {"refresh_interval": refresh_interval} if refresh_interval else {}
    store, _created = CacheStore.objects.get_or_create(key=key, defaults=defaults)
    if refresh_interval and store.refresh_interval != refresh_interval:
        store.refresh_interval = refresh_interval
        store.save(update_fields=["refresh_interval"])
    return store


def invalidate_cache_keys(keys: Iterable[str]) -> int:
    """Invalidate cache entries for the given keys and return the rows updated."""

    keys = tuple(keys)
    if not keys:
        return 0
    return CacheStore.objects.filter(key__in=keys).update(
        payload=None, refreshed_at=timezone.now()
    )
