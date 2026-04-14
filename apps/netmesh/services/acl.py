from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from apps.netmesh.models import PeerPolicy
from apps.ocpp.models import Charger


@dataclass(frozen=True, slots=True)
class ACLTaskSummary:
    """Resolved allow/deny task summary for a node pair."""

    policy_ids: list[int]
    allowed_tasks: list[str]
    denied_tasks: list[str]

    @property
    def allowed_services(self) -> list[str]:
        return self.allowed_tasks

    @property
    def denied_services(self) -> list[str]:
        return self.denied_tasks

    def allows(self, task_identifier: str) -> bool:
        task = (task_identifier or "").strip().lower()
        return bool(task and task in self.allowed_tasks and task not in self.denied_tasks)


class ACLResolver:
    """Resolve effective ACL permissions for scoped netmesh peers."""

    def __init__(self, *, tenant: str, site_id: int | None):
        self.tenant = tenant
        self.site_id = site_id
        policy_qs = PeerPolicy.objects.filter(tenant=tenant)
        if site_id:
            policy_qs = policy_qs.filter(site_id=site_id)
        else:
            policy_qs = policy_qs.filter(site__isnull=True)
        self.policies = list(policy_qs.select_related("source_group", "source_node", "destination_group", "destination_node"))

    def _station_ids_for_node(self, node) -> set[int]:
        return set(
            Charger.objects.filter(
                Q(manager_node=node) | Q(node_origin=node) | Q(forwarded_to=node)
            ).values_list("id", flat=True)
        )

    def _node_tags(self, node) -> set[str]:
        raw = getattr(node, "mesh_capability_flags", [])
        if not isinstance(raw, list):
            return set()
        return {str(tag).strip().lower() for tag in raw if isinstance(tag, str) and tag.strip()}

    def _selector_matches(self, *, node, group_id: int | None, station_ids: set[int], tags: set[str], side: str, policy: PeerPolicy) -> bool:
        node_id = getattr(policy, f"{side}_node_id")
        policy_group_id = getattr(policy, f"{side}_group_id")
        station_id = getattr(policy, f"{side}_station_id")
        policy_tags = set(getattr(policy, f"{side}_tags") or [])
        has_selector = bool(node_id or policy_group_id or station_id or policy_tags)

        if not has_selector:
            return False

        if node_id and node_id != node.id:
            return False
        if policy_group_id and policy_group_id != group_id:
            return False
        if station_id and station_id not in station_ids:
            return False
        if policy_tags and not policy_tags.issubset(tags):
            return False
        return True

    def resolve_pair(self, *, source_node, destination_node) -> ACLTaskSummary:
        source_group_id = getattr(source_node, "role_id", None)
        destination_group_id = getattr(destination_node, "role_id", None)
        source_station_ids = self._station_ids_for_node(source_node)
        destination_station_ids = self._station_ids_for_node(destination_node)
        source_tags = self._node_tags(source_node)
        destination_tags = self._node_tags(destination_node)

        allowed_tasks: set[str] = set()
        denied_tasks: set[str] = set()
        matched_policy_ids: list[int] = []

        for policy in self.policies:
            if not self._selector_matches(
                node=source_node,
                group_id=source_group_id,
                station_ids=source_station_ids,
                tags=source_tags,
                side="source",
                policy=policy,
            ):
                continue
            if not self._selector_matches(
                node=destination_node,
                group_id=destination_group_id,
                station_ids=destination_station_ids,
                tags=destination_tags,
                side="destination",
                policy=policy,
            ):
                continue

            matched_policy_ids.append(policy.id)
            allowed_tasks.update(policy.normalized_allowed_services())
            denied_tasks.update(policy.normalized_denied_services())

        return ACLTaskSummary(
            policy_ids=sorted(matched_policy_ids),
            allowed_tasks=sorted(task for task in allowed_tasks if task not in denied_tasks),
            denied_tasks=sorted(denied_tasks),
        )

    def resolve_task(self, *, source_node, destination_node, task_identifier: str) -> bool:
        return self.resolve_pair(source_node=source_node, destination_node=destination_node).allows(task_identifier)

    def resolve_service(self, *, source_node, destination_node, service_identifier: str) -> bool:
        return self.resolve_task(
            source_node=source_node,
            destination_node=destination_node,
            task_identifier=service_identifier,
        )
