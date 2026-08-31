import os
import functools
import asyncio


class OfflineError(RuntimeError):
    """Raised when a network operation is attempted in offline mode."""


def _is_offline() -> bool:
    flag = os.environ.get("ARTHEXIS_OFFLINE", "").lower()
    return flag not in ("", "0", "false", "no")


def requires_network(func):
    """Decorator that blocks execution when offline mode is enabled.

    When the environment variable ``ARTHEXIS_OFFLINE`` is set to a truthy value,
    any function decorated with ``@requires_network`` will raise
    :class:`OfflineError` before executing. Works with both synchronous and
    asynchronous callables.
    """

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            if _is_offline():
                raise OfflineError(f"{func.__name__} requires network access")
            return await func(*args, **kwargs)

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        if _is_offline():
            raise OfflineError(f"{func.__name__} requires network access")
        return func(*args, **kwargs)

    return sync_wrapper


def network_available() -> bool:
    """Return ``True`` if network operations are permitted."""

    return not _is_offline()


__all__ = ["OfflineError", "requires_network", "network_available"]
