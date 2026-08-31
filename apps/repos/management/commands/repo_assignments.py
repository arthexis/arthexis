"""Exchange repository work assignments with upstream/downstream nodes."""

from __future__ import annotations

import json

import requests
from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node
from apps.repos.services import work_assignments


class Command(BaseCommand):
    """Manage repository work assignment snapshots and upstream pulls."""

    help = "Exchange repository work assignments with upstream/downstream nodes."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)
        snapshot = subparsers.add_parser("snapshot", help="Print the local snapshot.")
        snapshot.add_argument("--pretty", action="store_true")
        pull = subparsers.add_parser(
            "pull-upstream",
            help="Post this node snapshot to an upstream and import assignments.",
        )
        pull.add_argument("--url", default="", help="Upstream base or sync URL.")
        pull.add_argument("--token", default="", help="Bearer token for upstream sync.")
        pull.add_argument("--timeout", type=int, default=0)
        pull.add_argument("--pretty", action="store_true")

    def handle(self, *args, **options):
        pretty = bool(options.get("pretty"))
        action = str(options["action"])
        handlers = {
            "snapshot": self._snapshot_payload,
            "pull-upstream": self._pull_upstream,
        }
        handler = handlers.get(action)
        if handler is None:
            raise CommandError(f"Unsupported action: {action}")
        self._write_json(handler(options), pretty=pretty)

    def _snapshot_payload(self, _options) -> dict[str, object]:
        payload = work_assignments.local_developer_snapshot()
        local_node = Node.get_local()
        payload["assignments"] = (
            work_assignments.assignments_for_node(
                local_node,
                capabilities=payload.get("capabilities"),
            )
            if local_node is not None
            else []
        )
        return payload

    def _pull_upstream(self, options) -> dict[str, object]:
        url = str(options.get("url") or "").strip()
        if not url:
            url = work_assignments.configured_upstream_url()
        if not url:
            raise CommandError(
                "Provide --url or configure REPOSITORY_WORK_ASSIGNMENT_UPSTREAM_URL."
            )
        token = str(options.get("token") or "").strip()
        if not token:
            token = work_assignments.configured_sync_token()
        timeout = self._parse_timeout(options)
        try:
            return work_assignments.pull_assignments_from_upstream(
                upstream_url=url,
                token=token,
                timeout=timeout,
            )
        except (requests.RequestException, work_assignments.AssignmentSyncError) as exc:
            raise CommandError(str(exc)) from exc

    def _parse_timeout(self, options) -> int | None:
        timeout = int(options.get("timeout") or 0)
        if timeout < 0:
            raise CommandError("--timeout must be greater than or equal to 0.")
        return timeout or None

    def _write_json(self, payload: dict[str, object], *, pretty: bool) -> None:
        if pretty:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(json.dumps(payload, sort_keys=True))
