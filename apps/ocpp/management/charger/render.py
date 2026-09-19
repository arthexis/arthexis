"""Safe human-readable renderer for charger snapshots."""

from datetime import datetime

from django.utils import timezone

from apps.ocpp.domain.snapshots import ChargerSnapshot


def render_snapshots(command, snapshots: list[ChargerSnapshot]) -> None:
    """Write a captured charger inventory without protocol payloads or secrets."""
    command.stdout.write(f"Charger snapshot: {timezone.now().isoformat()}")
    if not snapshots:
        command.stdout.write("No chargers found.")
        return
    rows = [_row(snapshot) for snapshot in snapshots]
    headers = {
        "identity": "Charger",
        "protocol": "Protocol",
        "connection": "Connection",
        "connectors": "Connectors",
        "sessions": "Active",
        "energy": "Energy total",
        "contact": "Last contact",
    }
    widths = {
        name: max(len(headers[name]), *(len(row[name]) for row in rows))
        for name in headers
    }
    command.stdout.write(_line(headers, widths))
    command.stdout.write(_line({name: "-" * widths[name] for name in headers}, widths))
    for row in rows:
        command.stdout.write(_line(row, widths))


def _row(snapshot: ChargerSnapshot) -> dict[str, str]:
    return {
        "identity": snapshot.identity,
        "protocol": snapshot.configured_protocol or "unconfigured",
        "connection": snapshot.connection_state,
        "connectors": ", ".join(snapshot.connector_states) or "-",
        "sessions": str(snapshot.active_transactions),
        "energy": _energy(snapshot),
        "contact": _timestamp(snapshot.last_contact),
    }


def _energy(snapshot: ChargerSnapshot) -> str:
    if snapshot.energy_kwh is None:
        return "unknown"
    unresolved = (
        f" + {snapshot.unresolved_sessions} unresolved"
        if snapshot.unresolved_sessions
        else ""
    )
    return f"{snapshot.energy_kwh:.4f} kWh{unresolved}"


def _timestamp(value: datetime | None) -> str:
    return timezone.localtime(value).isoformat() if value else "-"


def _line(values: dict[str, str], widths: dict[str, int]) -> str:
    return "  ".join(values[name].ljust(widths[name]) for name in values)
