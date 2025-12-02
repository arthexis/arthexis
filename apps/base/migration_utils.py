"""Stable helpers for migrations that rely on base app utilities."""

from django.contrib.auth.models import UserManager


class EntityUserManager(UserManager):
    """Minimal fallback manager for migration-time User proxies."""

    use_in_migrations = True
