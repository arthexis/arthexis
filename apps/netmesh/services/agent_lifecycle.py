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

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._request_shutdown)
        signal.signal(signal.SIGINT, self._request_shutdown)

    def __enter__(self):
        self.install_signal_handlers()
        self.mark_running(state="starting")
        return self

    def __exit__(self, exc_type, exc, tb):
        state = "stopped" if exc_type is None else "failed"
        self.mark_stopped(state=state, last_error=str(exc or ""))
        return False

    def _request_shutdown(self, signum, _frame):
        self.shutdown_requested = True
        self.mark_running(state=f"stopping:{signum}")

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
        session_count: int,
        relay_count: int,
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
        status.session_count = max(session_count, 0)
        status.relay_count = max(relay_count, 0)
        status.last_error = last_error[:500]
        status.save(
            update_fields=[
                "is_running",
                "lifecycle_state",
                "last_poll_at",
                "last_sync_at",
                "peers_synced",
                "session_count",
                "relay_count",
                "last_error",
            ]
        )
        return status

    @transaction.atomic
    def mark_stopped(self, *, state: str, last_error: str = "") -> NetmeshAgentStatus:
        status = NetmeshAgentStatus.get_solo()
        status.is_running = False
        status.lifecycle_state = state
        status.last_error = last_error[:500]
        status.save(update_fields=["is_running", "lifecycle_state", "last_error"])
        return status
