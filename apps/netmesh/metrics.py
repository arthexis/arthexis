"""Runtime and snapshot metrics for Netmesh monitoring outputs."""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from datetime import timedelta
from time import perf_counter

from django.db import DatabaseError
from django.db.models import Count, Q
from django.utils import timezone

from apps.netmesh.models import NetmeshAgentStatus, NodeEndpoint, NodeKeyMaterial
from apps.nodes.models import Node

_map_latency_state = {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0}


class map_generation_timer:
    """Context manager that records map generation latency."""

    def __enter__(self):
        self._started_at = perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            return False
        observe_map_generation_latency(perf_counter() - self._started_at)
        return False


def observe_map_generation_latency(seconds: float) -> None:
    """Record a generated map latency sample in seconds."""

    if seconds < 0:
        return
    _map_latency_state["count"] += 1
    _map_latency_state["total_seconds"] += float(seconds)
    _map_latency_state["max_seconds"] = max(_map_latency_state["max_seconds"], float(seconds))


def _map_latency_snapshot() -> dict[str, float | int]:
    count = int(_map_latency_state["count"])
    total_seconds = float(_map_latency_state["total_seconds"])
    return {
        "count": count,
        "avg_seconds": (total_seconds / count) if count else 0.0,
        "max_seconds": float(_map_latency_state["max_seconds"]),
    }


def _relay_only_ratio() -> dict[str, float | int]:
    try:
        endpoint_total = NodeEndpoint.objects.count()
        relay_required = NodeEndpoint.objects.filter(relay_required=True).count()
    except DatabaseError:
        endpoint_total = 0
        relay_required = 0
    ratio = (relay_required / endpoint_total) if endpoint_total else 0.0
    return {
        "relay_required": relay_required,
        "endpoint_total": endpoint_total,
        "ratio": ratio,
    }


def _key_age_distribution(now=None) -> dict[str, int]:
    now = now or timezone.now()
    buckets = Counter(
        {
            "lt_7_days": 0,
            "7_to_29_days": 0,
            "30_to_89_days": 0,
            "90_plus_days": 0,
        }
    )
    active_keys = NodeKeyMaterial.objects.filter(
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
        key_type=NodeKeyMaterial.KeyType.X25519,
    ).only("created_at")
    try:
        for key in active_keys:
            age = now - key.created_at
            if age < timedelta(days=7):
                buckets["lt_7_days"] += 1
            elif age < timedelta(days=30):
                buckets["7_to_29_days"] += 1
            elif age < timedelta(days=90):
                buckets["30_to_89_days"] += 1
            else:
                buckets["90_plus_days"] += 1
    except DatabaseError:
        return dict(buckets)
    return dict(buckets)


def _enrollment_counts() -> dict[str, int]:
    state_counts = defaultdict(int)
    try:
        for row in (
            Node.objects.values("mesh_enrollment_state")
            .order_by()
            .annotate(total=Count("id"))
        ):
            state_counts[str(row["mesh_enrollment_state"])] = int(row["total"])
    except DatabaseError:
        return dict(state_counts)
    return dict(state_counts)


def snapshot() -> dict[str, object]:
    """Return monitoring-friendly Netmesh metrics."""

    try:
        enrolled_nodes = Node.objects.filter(mesh_enrollment_state=Node.MeshEnrollmentState.ENROLLED).count()
        stale_endpoint_total = NodeEndpoint.objects.filter(
            Q(last_seen__isnull=True) | Q(last_seen__lt=timezone.now() - timedelta(hours=24))
        ).count()
    except DatabaseError:
        enrolled_nodes = 0
        stale_endpoint_total = 0
    try:
        agent_status = NetmeshAgentStatus.get_solo()
        agent_snapshot = {
            "is_running": agent_status.is_running,
            "lifecycle_state": agent_status.lifecycle_state,
            "last_poll_at": agent_status.last_poll_at.isoformat() if agent_status.last_poll_at else None,
            "peers_synced": agent_status.peers_synced,
            "session_count": agent_status.session_count,
            "relay_count": agent_status.relay_count,
        }
    except DatabaseError:
        agent_snapshot = {
            "is_running": False,
            "lifecycle_state": "unknown",
            "last_poll_at": None,
            "peers_synced": 0,
            "session_count": 0,
            "relay_count": 0,
        }
    return {
        "map_generation_latency_seconds": _map_latency_snapshot(),
        "enrolled_nodes": enrolled_nodes,
        "relay_only_ratio": _relay_only_ratio(),
        "key_age_distribution": _key_age_distribution(),
        "enrollment_states": _enrollment_counts(),
        "stale_endpoint_total": stale_endpoint_total,
        "agent": agent_snapshot,
    }
