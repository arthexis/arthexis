"""Optional automatic-capture contract for charger discovery.

Automatic capture is feature-scoped. Passive discovery does not depend on a
capture provider and remains useful when Gway is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import subprocess
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
    """Optional provider capable of planning and owning network interception."""

    name: str

    def plan(self, request: CaptureRequest) -> CapturePlan:
        """Return a mutation-free capture plan for a semantic request."""

    def apply(self, plan: CapturePlan) -> dict[str, object]:
        """Apply a planned redirect and return its durable provider handle."""

    def status(self, redirect_id: str) -> dict[str, object]:
        """Inspect one provider-owned redirect without changing it."""

    def release(self, redirect_id: str) -> dict[str, object]:
        """Remove one provider-owned redirect idempotently."""


class GwayCaptureProvider:
    """Optional Gway CLI provider for temporary charger-network redirects."""

    name = "gway"

    def __init__(self, executable: str = "gway", *, runner=None) -> None:
        self.executable = executable
        self.runner = runner or subprocess.run

    def _call(self, *arguments: str) -> dict[str, object]:
        completed = self.runner(
            [self.executable, *arguments, "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"Gway network capture failed: {detail or completed.returncode}")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("Gway network capture returned invalid JSON") from error
        if not isinstance(value, dict):
            raise RuntimeError("Gway network capture must return a JSON object")
        return value

    def available(self) -> bool:
        result = self._call("network", "available")
        return result.get("available") is True

    def plan(self, request: CaptureRequest) -> CapturePlan:
        if not self.available():
            raise RuntimeError("Gway network redirect capability is unavailable")
        return CapturePlan(
            provider=self.name,
            strategy="destination-redirect",
            request=request,
        )

    def apply(self, plan: CapturePlan) -> dict[str, object]:
        request = plan.request
        return self._call(
            "network",
            "redirect",
            request.interface,
            request.destination_ip,
            str(request.destination_port),
            "--target",
            request.local_host,
            "--target-port",
            str(request.local_port),
            "--sudo",
        )

    def status(self, redirect_id: str) -> dict[str, object]:
        return self._call("network", "status", redirect_id)

    def release(self, redirect_id: str) -> dict[str, object]:
        return self._call("network", "remove", redirect_id)


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
    """Return the optional Gway capture provider when its capability is usable."""

    executable = shutil.which("gway")
    if executable is None:
        return None
    provider = GwayCaptureProvider(executable)
    try:
        return provider if provider.available() else None
    except RuntimeError:
        return None
