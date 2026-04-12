"""Run the resident netmesh synchronization agent."""

from __future__ import annotations

import json
import os

from django.core.management.base import BaseCommand, CommandError

from apps.netmesh.services.agent import NetmeshAgentConfig, NetmeshAgentRuntime


class Command(BaseCommand):
    """Start a restart-safe resident netmesh agent loop."""

    help = (
        "Run a continuous netmesh agent loop that authenticates with enrollment identity, "
        "syncs mesh state, reconciles local state maps, and emits periodic health/status."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--api-base-url",
            default=os.environ.get("NETMESH_API_BASE_URL", "http://127.0.0.1:8000/api/netmesh"),
            help="Base URL for Netmesh API (default: NETMESH_API_BASE_URL or http://127.0.0.1:8000/api/netmesh).",
        )
        parser.add_argument(
            "--enrollment-token",
            default=os.environ.get("NETMESH_ENROLLMENT_TOKEN", ""),
            help="Enrollment/bootstrap token used as Bearer auth (default: NETMESH_ENROLLMENT_TOKEN).",
        )
        parser.add_argument("--poll-interval", type=float, default=30.0, help="Poll interval in seconds.")
        parser.add_argument("--keepalive-interval", type=float, default=60.0, help="Keepalive interval in seconds.")
        parser.add_argument("--rekey-interval", type=float, default=300.0, help="Rekey interval in seconds.")
        parser.add_argument(
            "--endpoint-refresh-interval",
            type=float,
            default=120.0,
            help="Endpoint refresh interval in seconds.",
        )
        parser.add_argument("--request-timeout", type=float, default=10.0, help="API request timeout in seconds.")
        parser.add_argument(
            "--max-loops",
            type=int,
            default=None,
            help="Optional loop bound for smoke tests and controlled runs.",
        )

    def handle(self, *args, **options):
        token = str(options["enrollment_token"] or "").strip()
        if not token:
            raise CommandError("An enrollment token is required via --enrollment-token or NETMESH_ENROLLMENT_TOKEN.")

        config = NetmeshAgentConfig(
            api_base_url=options["api_base_url"],
            enrollment_token=token,
            poll_interval_seconds=max(float(options["poll_interval"]), 0.1),
            keepalive_interval_seconds=max(float(options["keepalive_interval"]), 0.1),
            rekey_interval_seconds=max(float(options["rekey_interval"]), 0.1),
            endpoint_refresh_interval_seconds=max(float(options["endpoint_refresh_interval"]), 0.1),
            request_timeout_seconds=max(float(options["request_timeout"]), 0.1),
            max_loops=options["max_loops"],
        )
        runtime = NetmeshAgentRuntime(config=config)
        loops_completed = runtime.run()
        status_payload = {
            "status": "stopped",
            "loops_completed": loops_completed,
            "peers_synced": len(runtime.store.peer_map),
            "session_count": len(runtime.store.session_map),
            "relay_count": len(runtime.store.relay_map),
        }
        self.stdout.write(json.dumps(status_payload, sort_keys=True))
