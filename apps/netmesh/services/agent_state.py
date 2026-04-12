"""In-memory state maps used by the long-running netmesh agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NetmeshStateStore:
    """Holds current peer/session/relay maps with deterministic reconciliation."""

    peer_map: dict[int, dict[str, object]] = field(default_factory=dict)
    session_map: dict[str, dict[str, object]] = field(default_factory=dict)
    relay_map: dict[int, list[dict[str, object]]] = field(default_factory=dict)

    def reconcile_peers(self, peers: list[dict[str, object]]) -> int:
        """Replace peer map from API payload and return active peer count."""

        next_peers: dict[int, dict[str, object]] = {}
        for peer in peers:
            peer_id = peer.get("node_id")
            if not isinstance(peer_id, int):
                continue
            next_peers[peer_id] = peer
        self.peer_map = next_peers
        return len(self.peer_map)

    def reconcile_endpoints(self, endpoints: list[dict[str, object]]) -> tuple[int, int]:
        """Refresh session and relay maps from endpoint payload.

        Sessions are keyed by ``"<node_id>:<endpoint>"`` to guarantee idempotent replacement
        on each poll, while relay options are grouped by peer node id.
        """

        next_sessions: dict[str, dict[str, object]] = {}
        next_relays: dict[int, list[dict[str, object]]] = {}
        for endpoint in endpoints:
            node_id = endpoint.get("node_id")
            endpoint_url = endpoint.get("endpoint")
            if not isinstance(node_id, int) or not isinstance(endpoint_url, str) or not endpoint_url:
                continue

            session_key = f"{node_id}:{endpoint_url}"
            next_sessions[session_key] = endpoint

            candidates = endpoint.get("connection_candidates")
            if not isinstance(candidates, list):
                continue
            relay_candidates = [
                candidate
                for candidate in candidates
                if isinstance(candidate, dict) and candidate.get("path") == "relay"
            ]
            if relay_candidates:
                next_relays[node_id] = relay_candidates

        self.session_map = next_sessions
        self.relay_map = next_relays
        return len(self.session_map), len(self.relay_map)
