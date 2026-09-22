"""Safe human-readable renderer for charger snapshots."""

from datetime import datetime

from django.utils import timezone

from apps.ocpp.domain.snapshots import ChargerSnapshot


def render_snapshots(
    command,
    snapshots: list[ChargerSnapshot],
    *,
    detail: bool = False,
) -> None:
    """Write a captured charger inventory without protocol payloads or secrets."""
    command.stdout.write(f"Fleet snapshot: {timezone.now().isoformat()}")
    if not snapshots:
        command.stdout.write("No chargers found.")
        return
    rows = [_row(snapshot, detail=detail) for snapshot in snapshots]
    headers = {
        "identity": "Charger",
        "enabled": "Enabled",
        "connection": "Connection",
        "state": "State",
        "protocol": "Protocol",
        "current": "Active TX",
        "last": "Last TX",
        "contact": "Last contact",
    }
    if detail:
        headers.update(
            {
                "connectors": "Connectors",
                "current_started": "Active since",
                "last_stopped": "Last stopped",
                "energy": "Energy total",
                "unresolved": "Recovery unresolved",
                "energy_unresolved": "Energy unresolved",
                "cutover": "Authority cutover",
                "historical": "Historical TX",
                "historical_open": "Historical open",
                "historical_oldest": "Historical oldest",
                "historical_latest": "Historical latest",
            }
        )
    widths = {
        name: max(len(headers[name]), *(len(row[name]) for row in rows))
        for name in headers
    }
    command.stdout.write(_line(headers, widths))
    command.stdout.write(_line({name: "-" * widths[name] for name in headers}, widths))
    for row in rows:
        command.stdout.write(_line(row, widths))


def _row(snapshot: ChargerSnapshot, *, detail: bool = False) -> dict[str, str]:
    row = {
        "identity": snapshot.identity,
        "enabled": "yes" if snapshot.enabled else "no",
        "connection": snapshot.connection_state,
        "state": snapshot.state,
        "protocol": snapshot.configured_protocol or "unconfigured",
        "current": snapshot.current_transaction_id or "-",
        "last": snapshot.last_transaction_id or "-",
        "contact": _timestamp(snapshot.last_contact),
    }
    if detail:
        row.update(
            {
                "connectors": ", ".join(snapshot.connector_states) or "-",
                "current_started": _timestamp(snapshot.current_transaction_started),
                "last_stopped": _timestamp(snapshot.last_transaction_stopped),
                "energy": _energy(snapshot),
                "unresolved": str(snapshot.unresolved_sessions),
                "energy_unresolved": str(snapshot.unresolved_energy_sessions),
                "cutover": _timestamp(snapshot.authority_cutover_at),
                "historical": str(snapshot.historical_sessions),
                "historical_open": str(snapshot.historical_open_sessions),
                "historical_oldest": _timestamp(snapshot.historical_oldest_started),
                "historical_latest": _timestamp(snapshot.historical_latest_activity),
            }
        )
    return row


def _energy(snapshot: ChargerSnapshot) -> str:
    if snapshot.energy_kwh is None:
        return "unknown"
    return f"{snapshot.energy_kwh:.4f} kWh"


def _timestamp(value: datetime | None) -> str:
    return timezone.localtime(value).isoformat() if value else "-"


def _line(values: dict[str, str], widths: dict[str, int]) -> str:
    return "  ".join(values[name].ljust(widths[name]) for name in values)
