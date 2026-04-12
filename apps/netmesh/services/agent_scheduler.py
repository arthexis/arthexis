"""Interval scheduler for netmesh agent keepalive/rekey/refresh cycles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone


@dataclass
class ScheduledTask:
    """Task descriptor storing cadence and next run timestamp."""

    interval: timedelta
    next_run_at: object = field(default_factory=timezone.now)


class NetmeshAgentScheduler:
    """Tracks periodic task execution windows for the netmesh agent."""

    def __init__(
        self,
        *,
        keepalive_interval: timedelta,
        rekey_interval: timedelta,
        endpoint_refresh_interval: timedelta,
    ):
        now = timezone.now()
        self._tasks = {
            "keepalive": ScheduledTask(interval=keepalive_interval, next_run_at=now),
            "rekey": ScheduledTask(interval=rekey_interval, next_run_at=now),
            "endpoint_refresh": ScheduledTask(interval=endpoint_refresh_interval, next_run_at=now),
        }

    def due_tasks(self, *, now=None) -> list[str]:
        """Return all task names due as of ``now`` and roll next run windows."""

        now = now or timezone.now()
        due: list[str] = []
        for name, task in self._tasks.items():
            if now >= task.next_run_at:
                due.append(name)
                task.next_run_at = now + task.interval
        return due
