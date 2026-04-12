"""Resident netmesh agent runtime that continuously syncs API state."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timedelta
from time import sleep

from django.utils import timezone

from apps.netmesh.metrics import snapshot
from apps.netmesh.services.agent_lifecycle import NetmeshLifecycle
from apps.netmesh.services.agent_scheduler import NetmeshAgentScheduler
from apps.netmesh.services.agent_state import NetmeshStateStore

logger = logging.getLogger("apps.netmesh.agent")


@dataclass
class NetmeshAgentConfig:
    """Configuration for long-running netmesh agent polling behavior."""

    api_base_url: str
    enrollment_token: str
    poll_interval_seconds: float = 30.0
    keepalive_interval_seconds: float = 60.0
    rekey_interval_seconds: float = 300.0
    endpoint_refresh_interval_seconds: float = 120.0
    request_timeout_seconds: float = 10.0
    max_loops: int | None = None


class NetmeshAgentRuntime:
    """Continuous state-sync loop that rebuilds local state from API responses."""

    def __init__(self, *, config: NetmeshAgentConfig):
        self.config = config
        self.store = NetmeshStateStore()
        self.lifecycle = NetmeshLifecycle()
        self.scheduler = NetmeshAgentScheduler(
            keepalive_interval=timedelta(seconds=config.keepalive_interval_seconds),
            rekey_interval=timedelta(seconds=config.rekey_interval_seconds),
            endpoint_refresh_interval=timedelta(seconds=config.endpoint_refresh_interval_seconds),
        )

    def _request_json(self, path: str) -> dict[str, object]:
        url = f"{self.config.api_base_url.rstrip('/')}/{path.lstrip('/')}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.enrollment_token}",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"{path} HTTP {exc.code}: {detail[:200]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{path} unavailable: {exc.reason}") from exc
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{path} returned non-object payload")
        return data

    def _sync_cycle(self) -> dict[str, int]:
        peers_payload = self._request_json("peers/")
        endpoints_payload = self._request_json("peer-endpoints/")

        peers = peers_payload.get("peers")
        endpoints = endpoints_payload.get("endpoints")
        peers_synced = self.store.reconcile_peers(peers if isinstance(peers, list) else [])
        session_count, relay_count = self.store.reconcile_endpoints(endpoints if isinstance(endpoints, list) else [])
        return {
            "peers_synced": peers_synced,
            "session_count": session_count,
            "relay_count": relay_count,
        }

    def _run_due_tasks(self) -> None:
        for task_name in self.scheduler.due_tasks(now=timezone.now()):
            if task_name == "keepalive":
                logger.info("Netmesh agent keepalive", extra={"event": "netmesh.agent.keepalive", "metrics": snapshot()})
            elif task_name == "rekey":
                logger.info("Netmesh agent rekey check", extra={"event": "netmesh.agent.rekey"})
            elif task_name == "endpoint_refresh":
                logger.info("Netmesh agent endpoint refresh", extra={"event": "netmesh.agent.endpoint_refresh"})

    def run(self) -> int:
        """Run until shutdown signal or max loop count is reached."""

        loops_completed = 0
        with self.lifecycle:
            self.lifecycle.mark_running(state="running")
            while not self.lifecycle.shutdown_requested:
                if self.config.max_loops is not None and loops_completed >= self.config.max_loops:
                    break
                try:
                    counters = self._sync_cycle()
                    self.lifecycle.mark_progress(**counters)
                    self._run_due_tasks()
                    logger.info(
                        "Netmesh agent sync completed",
                        extra={
                            "event": "netmesh.agent.sync",
                            **counters,
                        },
                    )
                except Exception as exc:
                    self.lifecycle.mark_progress(
                        peers_synced=len(self.store.peer_map),
                        session_count=len(self.store.session_map),
                        relay_count=len(self.store.relay_map),
                        lifecycle_state="degraded",
                        last_error=str(exc),
                    )
                    logger.exception("Netmesh agent sync failed", extra={"event": "netmesh.agent.sync_failed"})

                loops_completed += 1
                if self.config.max_loops is not None and loops_completed >= self.config.max_loops:
                    break
                remaining = max(self.config.poll_interval_seconds, 0.1)
                while remaining > 0 and not self.lifecycle.shutdown_requested:
                    step = min(remaining, 1.0)
                    sleep(step)
                    remaining -= step

        return loops_completed
