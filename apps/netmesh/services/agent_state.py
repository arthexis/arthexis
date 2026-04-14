"""In-memory state maps used by the long-running netmesh agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NetmeshStateStore:
    """Holds current peer_map with deterministic reconciliation."""

    peer_map: dict[int, dict[str, object]] = field(default_factory=dict)

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
