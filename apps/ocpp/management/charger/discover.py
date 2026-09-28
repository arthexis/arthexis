"""Foreground passive charger discovery operation."""

from pathlib import Path

from django.conf import settings

from apps.ocpp.discovery.capture import (
    capture_request_from_candidate,
    default_capture_provider,
)
from apps.ocpp.discovery.network import TsharkObserver, run_passive_discovery
from apps.ocpp.discovery.render import render_discovery_events


def discover(
    interface: str,
    role: str | None = None,
    root: str | None = None,
    capture: bool = False,
) -> dict[str, object]:
    """Observe a charger-facing interface and optionally prepare automatic capture."""

    session = run_passive_discovery(
        interface=interface,
        role=role,
        root=Path(root) if root else Path(settings.DATA_DIR),
        observer=TsharkObserver(),
    )
    if capture:
        events = session.events()
        candidate = next(
            (
                event["metadata"]
                for event in events
                if event["event_type"] == "csms_candidate"
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
        else:
            provider = default_capture_provider()
            if provider is None:
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
            else:
                request = capture_request_from_candidate(
                    interface=interface,
                    candidate=candidate,
                    local_host="127.0.0.1",
                    local_port=9000,
                )
                plan = provider.plan(request)
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

    summary = session.write_summary()
    return {
        **summary,
        "display": render_discovery_events(session.session_id, session.events()),
    }
