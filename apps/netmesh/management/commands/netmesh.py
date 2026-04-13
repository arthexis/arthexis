"""Netmesh lifecycle operations and monitoring output."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.netmesh.metrics import snapshot
from apps.netmesh.models import NodeKeyMaterial, PeerPolicy
from apps.nodes.models import Node, NodeEnrollmentEvent
from apps.nodes.services.enrollment import issue_enrollment_token


class Command(BaseCommand):
    """Provide a single-word command for common netmesh operations."""

    help = "Netmesh operations: token enrollment, key rotation scheduling, policy checks, and health metrics."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        enroll_parser = subparsers.add_parser("enroll-token", help="Generate an enrollment token for a node.")
        enroll_parser.add_argument("node", help="Node id, UUID, or public endpoint.")
        enroll_parser.add_argument("--scope", default="mesh:read", help="Enrollment scope (default: mesh:read).")
        enroll_parser.add_argument("--ttl-minutes", type=int, default=60, help="Token TTL minutes (default: 60).")
        enroll_parser.add_argument("--site-domain", default="", help="Optional site domain override.")

        rotate_parser = subparsers.add_parser(
            "schedule-rotation",
            help="Schedule key rotation events for nodes with aged active keys.",
        )
        rotate_parser.add_argument("--max-age-days", type=int, default=30, help="Key age threshold in days.")
        rotate_parser.add_argument("--dry-run", action="store_true", help="Report candidates without writing events.")

        policy_parser = subparsers.add_parser("policy", help="Compile and validate policy rules.")
        policy_parser.add_argument("mode", choices=("compile", "check"))
        policy_parser.add_argument("--tenant", default="", help="Optional tenant filter.")
        policy_parser.add_argument("--site-id", type=int, default=None, help="Optional site id filter.")

        health_parser = subparsers.add_parser("health", help="Emit monitoring-friendly health and metrics output.")
        health_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "enroll-token":
            return self._handle_enroll_token(**options)
        if action == "schedule-rotation":
            return self._handle_schedule_rotation(**options)
        if action == "policy":
            return self._handle_policy(**options)
        if action == "health":
            return self._handle_health(**options)
        raise CommandError(f"Unsupported action: {action}")

    def _resolve_node(self, selector: str) -> Node:
        selector = str(selector or "").strip()
        if not selector:
            raise CommandError("Node selector is required.")
        filters = Q(public_endpoint=selector)
        if selector.isdigit():
            filters |= Q(id=int(selector))
        try:
            parsed_uuid = uuid.UUID(selector)
        except ValueError:
            parsed_uuid = None
        if parsed_uuid is not None:
            filters |= Q(uuid=parsed_uuid)
        node = Node.objects.filter(filters).first()
        if node is None:
            raise CommandError(f"Node not found for selector: {selector}")
        return node

    def _handle_enroll_token(self, **options):
        node = self._resolve_node(options["node"])
        ttl_minutes = int(options["ttl_minutes"])
        if ttl_minutes <= 0:
            raise CommandError("--ttl-minutes must be greater than zero.")
        site = None
        site_domain = str(options["site_domain"] or "").strip()
        if site_domain:
            site = Site.objects.filter(domain__iexact=site_domain).first()
            if site is None:
                raise CommandError(f"Site not found for domain: {site_domain}")
        enrollment, token = issue_enrollment_token(
            node=node,
            site=site,
            ttl=timedelta(minutes=ttl_minutes),
            scope=options["scope"],
        )
        payload = {
            "node_id": node.id,
            "public_endpoint": node.public_endpoint,
            "token": token,
            "scope": enrollment.scope,
            "expires_at": enrollment.expires_at.isoformat(),
            "enrollment_id": enrollment.id,
        }
        self.stdout.write(json.dumps(payload, sort_keys=True))

    def _handle_schedule_rotation(self, **options):
        max_age_days = int(options["max_age_days"])
        if max_age_days <= 0:
            raise CommandError("--max-age-days must be greater than zero.")
        now = timezone.now()
        cutoff = now - timedelta(days=max_age_days)
        due_keys = list(
            NodeKeyMaterial.objects.filter(
                key_state=NodeKeyMaterial.KeyState.ACTIVE,
                key_type=NodeKeyMaterial.KeyType.X25519,
                created_at__lt=cutoff,
            )
            .select_related("node")
            .order_by("created_at")
        )
        if options["dry_run"]:
            self.stdout.write(json.dumps({"scheduled": 0, "candidates": [key.node_id for key in due_keys]}))
            return
        scheduled = 0
        for key in due_keys:
            NodeEnrollmentEvent.objects.create(
                node=key.node,
                enrollment=key.node.enrollments.order_by("-created_at").first(),
                action=NodeEnrollmentEvent.Action.KEY_ROTATED,
                from_state=key.node.mesh_enrollment_state,
                to_state=key.node.mesh_enrollment_state,
                details={
                    "scheduled_for": now.isoformat(),
                    "reason": f"active key older than {max_age_days} days",
                    "key_material_id": key.id,
                },
            )
            scheduled += 1
        self.stdout.write(json.dumps({"scheduled": scheduled, "threshold_days": max_age_days}))

    def _handle_policy(self, **options):
        tenant = str(options["tenant"] or "").strip()
        site_id = options["site_id"]
        policies = PeerPolicy.objects.all()
        if tenant:
            policies = policies.filter(tenant=tenant)
        if site_id is not None:
            policies = policies.filter(site_id=site_id)
        policies = policies.select_related("source_node", "destination_node", "source_group", "destination_group")

        compiled: list[dict[str, object]] = []
        errors: list[dict[str, object]] = []
        for policy in policies:
            try:
                policy.full_clean()
            except ValidationError as exc:
                errors.append({"policy_id": policy.id, "error": str(exc)})
                continue
            compiled.append(
                {
                    "policy_id": policy.id,
                    "tenant": policy.tenant,
                    "site_id": policy.site_id,
                    "source": {
                        "node_id": policy.source_node_id,
                        "group_id": policy.source_group_id,
                        "station_id": policy.source_station_id,
                        "tags": policy.normalized_source_tags(),
                    },
                    "destination": {
                        "node_id": policy.destination_node_id,
                        "group_id": policy.destination_group_id,
                        "station_id": policy.destination_station_id,
                        "tags": policy.normalized_destination_tags(),
                    },
                    "allowed_services": policy.normalized_allowed_services(),
                    "denied_services": policy.normalized_denied_services(),
                }
            )
        payload = {
            "mode": options["mode"],
            "policy_count": len(compiled),
            "compiled": compiled if options["mode"] == "compile" else [],
            "errors": errors,
        }
        self.stdout.write(json.dumps(payload, sort_keys=True))
        if options["mode"] == "check" and errors:
            raise CommandError(f"Policy check failed for {len(errors)} policy entries.")

    def _handle_health(self, **options):
        health_payload = {
            "status": "ok",
            "timestamp": timezone.now().isoformat(),
            "metrics": snapshot(),
        }
        if options["json"]:
            self.stdout.write(json.dumps(health_payload, sort_keys=True))
            return
        self.stdout.write(f"status={health_payload['status']}")
        self.stdout.write(f"timestamp={health_payload['timestamp']}")
        self.stdout.write(json.dumps(health_payload["metrics"], sort_keys=True))
