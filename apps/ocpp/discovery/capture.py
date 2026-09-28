"""Optional automatic-capture contract for charger discovery.

Automatic capture is feature-scoped. Passive discovery does not depend on a
capture provider and remains useful when Gway is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CaptureRequest:
    """Semantic request to redirect one discovered CSMS destination locally."""

    interface: str
    destination_ip: str
    destination_port: int
    local_host: str
    local_port: int
    destination_mac: str | None = None
    hostname: str | None = None


@dataclass(frozen=True)
class CapturePlan:
    """Pure provider plan for satisfying a capture request without applying it."""

    provider: str
    strategy: str
    request: CaptureRequest


class CaptureProvider(Protocol):
    """Optional provider capable of planning automatic network interception."""

    name: str

    def plan(self, request: CaptureRequest) -> CapturePlan:
        """Return a mutation-free capture plan for a semantic request."""


def capture_request_from_candidate(
    *,
    interface: str,
    candidate: dict[str, object],
    local_host: str,
    local_port: int,
) -> CaptureRequest:
    """Build a capture request from one persisted CSMS candidate."""

    destination_ip = candidate.get("destination_ip")
    destination_port = candidate.get("destination_port")
    if not isinstance(destination_ip, str) or not destination_ip:
        raise ValueError("CSMS candidate is missing destination_ip")
    if not isinstance(destination_port, int):
        raise ValueError("CSMS candidate is missing destination_port")

    destination_mac = candidate.get("destination_mac")
    if not isinstance(destination_mac, str):
        destination_mac = None

    hostname = None
    hostnames = candidate.get("hostnames")
    if isinstance(hostnames, list):
        hostname = next(
            (value for value in hostnames if isinstance(value, str) and value),
            None,
        )

    return CaptureRequest(
        interface=interface,
        destination_ip=destination_ip,
        destination_port=destination_port,
        destination_mac=destination_mac,
        hostname=hostname,
        local_host=local_host,
        local_port=local_port,
    )


def default_capture_provider() -> CaptureProvider | None:
    """Return the optional automatic-capture provider when one is installed.

    Gway integration is intentionally implemented in a later chunk. Returning
    None here preserves independent Arthexis operation and keeps passive
    discovery available without Gway.
    """

    return None
