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
        result = value.get("result")
        if isinstance(result, dict):
            return result
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
            "capture",
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



def active_redirect(events) -> dict[str, object] | None:
    """Return the latest redirect still owned by this discovery session."""

    active = None
    for event in events:
        event_type = event.get("event_type")
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if event_type == "capture_started":
            active = metadata
        elif event_type == "capture_released":
            active = None
    return active


def start_capture(
    session,
    *,
    interface: str,
    provider: CaptureProvider | None = None,
    local_host: str = "127.0.0.1",
    local_port: int = 9000,
) -> dict[str, object]:
    """Apply one optional provider redirect and persist Arthexis ownership."""

    events = session.events()
    existing = active_redirect(events)
    if existing is not None:
        return dict(existing)

    candidate = next(
        (
            event["metadata"]
            for event in events
            if event.get("event_type") == "csms_candidate"
            and isinstance(event.get("metadata"), dict)
        ),
        None,
    )
    session.record(
        "capture_requested",
        metadata={"interface": interface, "candidate_available": candidate is not None},
    )
    if not isinstance(candidate, dict):
        session.record(
            "capture_unavailable",
            metadata={"reason": "no_csms_candidate"},
        )
        return {"active": False, "reason": "no_csms_candidate"}

    selected = provider or default_capture_provider()
    if selected is None:
        session.record(
            "capture_unavailable",
            metadata={
                "reason": "capture_provider_unavailable",
                "detail": (
                    "Automatic capture requires the optional Gway network "
                    "capture capability. Passive discovery completed normally."
                ),
            },
        )
        return {"active": False, "reason": "capture_provider_unavailable"}

    request = capture_request_from_candidate(
        interface=interface,
        candidate=candidate,
        local_host=local_host,
        local_port=local_port,
    )
    plan = selected.plan(request)

    source_ips = candidate.get("source_ips")
    client_hosts = (
        [value for value in source_ips if isinstance(value, str) and value]
        if isinstance(source_ips, list)
        else []
    )
    if len(client_hosts) != 1:
        session.record(
            "capture_unavailable",
            metadata={
                "reason": "handoff_match_unavailable",
                "source_ips": client_hosts,
            },
        )
        return {"active": False, "reason": "handoff_match_unavailable"}

    original_destination = {
        "host": request.hostname or request.destination_ip,
        "ip": request.destination_ip,
        "port": request.destination_port,
    }
    session.arm_handoff(
        client_host=client_hosts[0],
        original_destination=original_destination,
        strategy=plan.strategy,
    )

    session.record(
        "capture_available",
        metadata={
            "provider": plan.provider,
            "strategy": plan.strategy,
            "destination_ip": request.destination_ip,
            "destination_port": request.destination_port,
            "local_host": request.local_host,
            "local_port": request.local_port,
        },
    )
    try:
        applied = selected.apply(plan)
    except Exception as error:
        session.disarm_handoff()
        session.record(
            "capture_failed",
            metadata={
                "reason": "redirect_apply_failed",
                "detail": str(error),
                "provider": plan.provider,
                "strategy": plan.strategy,
            },
        )
        return {"active": False, "reason": "redirect_apply_failed"}

    redirect_id = applied.get("id")
    if not isinstance(redirect_id, str) or not redirect_id:
        session.record(
            "capture_failed",
            metadata={
                "reason": "invalid_redirect_handle",
                "provider": plan.provider,
                "strategy": plan.strategy,
            },
        )
        return {"active": False, "reason": "invalid_redirect_handle"}

    ownership = {
        "provider": plan.provider,
        "strategy": plan.strategy,
        "redirect_id": redirect_id,
        "destination_ip": request.destination_ip,
        "destination_port": request.destination_port,
        "local_host": request.local_host,
        "local_port": request.local_port,
    }
    session.record("capture_started", metadata=ownership)
    return {**ownership, "active": True}


def release_capture(
    session,
    *,
    provider: CaptureProvider | None = None,
) -> dict[str, object]:
    """Release the redirect currently owned by one discovery session."""

    ownership = active_redirect(session.events())
    if ownership is None:
        return {"active": False, "changed": False}

    redirect_id = ownership.get("redirect_id")
    if not isinstance(redirect_id, str) or not redirect_id:
        session.record(
            "capture_release_failed",
            metadata={"reason": "missing_redirect_handle"},
        )
        return {
            "active": True,
            "changed": False,
            "reason": "missing_redirect_handle",
        }

    session.record(
        "capture_release_requested",
        metadata={"redirect_id": redirect_id},
    )
    selected = provider or default_capture_provider()
    if selected is None:
        session.record(
            "capture_release_failed",
            metadata={
                "reason": "capture_provider_unavailable",
                "redirect_id": redirect_id,
            },
        )
        return {
            "active": True,
            "changed": False,
            "reason": "capture_provider_unavailable",
            "redirect_id": redirect_id,
        }

    try:
        result = selected.release(redirect_id)
    except Exception as error:
        session.record(
            "capture_release_failed",
            metadata={
                "reason": "redirect_remove_failed",
                "detail": str(error),
                "redirect_id": redirect_id,
            },
        )
        return {
            "active": True,
            "changed": False,
            "reason": "redirect_remove_failed",
            "redirect_id": redirect_id,
        }

    session.record(
        "capture_released",
        metadata={
            "redirect_id": redirect_id,
            "changed": bool(result.get("changed", False)),
        },
    )
    handoff = session.disarm_handoff()
    return {
        "active": False,
        "changed": bool(result.get("changed", False)),
        "redirect_id": redirect_id,
        "handoff_disarmed": bool(handoff.get("disarmed", False)),
        "handoff_claimed": bool(handoff.get("claimed", False)),
    }
