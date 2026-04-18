"""Lifecycle hooks for restart-safe netmesh agent operation."""

from __future__ import annotations

import signal
from contextlib import AbstractContextManager
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.netmesh.models import NetmeshAgentStatus


@dataclass
class NetmeshLifecycle(AbstractContextManager):
    """Tracks process lifecycle and handles cooperative signal-based shutdown."""

    shutdown_requested: bool = False
    shutdown_signal: int | None = None
    _previous_sigterm_handler: object | None = None
    _previous_sigint_handler: object | None = None

    def install_signal_handlers(self) -> None:
        self._previous_sigterm_handler = signal.signal(signal.SIGTERM, self._request_shutdown)
        self._previous_sigint_handler = signal.signal(signal.SIGINT, self._request_shutdown)

    def restore_signal_handlers(self) -> None:
        if self._previous_sigterm_handler is not None:
            signal.signal(signal.SIGTERM, self._previous_sigterm_handler)
            self._previous_sigterm_handler = None
        if self._previous_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._previous_sigint_handler)
            self._previous_sigint_handler = None

    def __enter__(self):
        self.install_signal_handlers()
        self.mark_running(state="starting")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.restore_signal_handlers()
        state = "stopped" if exc_type is None else "failed"
        last_error = str(exc) if exc_type is not None else None
        self.mark_stopped(state=state, last_error=last_error)
        return False

    def _request_shutdown(self, signum, _frame):
        self.shutdown_requested = True
        self.shutdown_signal = signum

    @transaction.atomic
    def mark_running(self, *, state: str) -> NetmeshAgentStatus:
        status = NetmeshAgentStatus.get_solo()
        status.is_running = True
        status.lifecycle_state = state
        status.save(update_fields=["is_running", "lifecycle_state"])
        return status

    @transaction.atomic
    def mark_progress(
        self,
        *,
        peers_synced: int,
        lifecycle_state: str = "running",
        last_error: str = "",
    ) -> NetmeshAgentStatus:
        status = NetmeshAgentStatus.get_solo()
        now = timezone.now()
        status.is_running = True
        status.lifecycle_state = lifecycle_state
        status.last_poll_at = now
        status.last_sync_at = now
        status.peers_synced = max(peers_synced, 0)
        status.last_error = last_error[:500]
        status.save(
            update_fields=[
                "is_running",
                "lifecycle_state",
                "last_poll_at",
                "last_sync_at",
                "peers_synced",
                "last_error",
            ]
        )
        return status

    @transaction.atomic
    def mark_stopped(self, *, state: str, last_error: str | None = None) -> NetmeshAgentStatus:
        status = NetmeshAgentStatus.get_solo()
        status.is_running = False
        status.lifecycle_state = state
        update_fields = ["is_running", "lifecycle_state"]
        if last_error is not None:
            status.last_error = last_error[:500]
            update_fields.append("last_error")
        status.save(update_fields=update_fields)
        return status
