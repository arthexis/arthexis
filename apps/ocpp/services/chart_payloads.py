"""Chart payload helpers for public charger status views and APIs."""

from __future__ import annotations

from typing import Any

from django.http import Http404

from apps.ocpp.models import Transaction

from .chargers import (
    connector_set,
    ensure_charger_access,
    get_charger_for_read,
    live_sessions,
)


class ChargerAccessDeniedError(PermissionError):
    """Raised when a user cannot access the requested charger."""


def _series_from_transaction(tx: Transaction) -> list[tuple[str, float]]:
    """Build cumulative kWh points from a transaction's meter readings.

    Parameters:
        tx: Transaction whose meter values should be transformed into chart points.

    Returns:
        list[tuple[str, float]]: ISO timestamp and cumulative kWh pairs.
    """

    points: list[tuple[str, float]] = []
    readings = list(
        tx.meter_values.filter(energy__isnull=False).order_by("timestamp")
    )
    start_val = float(tx.meter_start) / 1000.0 if tx.meter_start is not None else None
    for reading in readings:
        try:
            val = float(reading.energy)
        except (TypeError, ValueError):
            continue
        if start_val is None:
            start_val = val
        points.append((reading.timestamp.isoformat(), max(val - start_val, 0.0)))
    return points


def build_charger_chart_payload(
    *,
    user,
    cid: str,
    connector: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return the chart payload consumed by the charger status UI.

    Parameters:
        user: Authenticated user requesting charger data.
        cid: Charger identifier.
        connector: Optional connector slug.
        session_id: Optional transaction identifier for historic sessions.

    Returns:
        dict[str, Any]: JSON-serializable chart payload with labels and datasets.

    Raises:
        ChargerAccessDeniedError: If the user is not allowed to view the charger.
        Transaction.DoesNotExist: If a requested historic session cannot be found.
    """

    try:
        charger, _connector_slug = get_charger_for_read(cid, connector)
        ensure_charger_access(user, charger)
    except Http404 as exc:
        raise ChargerAccessDeniedError("User cannot access this charger") from exc

    connectors = [item for item in connector_set(charger) if item.is_visible_to(user)]
    sessions = live_sessions(charger, connectors=connectors)
    tx_obj = None
    past_session = False

    if session_id:
        if charger.connector_id is None:
            tx_obj = Transaction.objects.filter(
                pk=session_id,
                charger__charger_id=cid,
            ).first()
            if tx_obj is None:
                raise Transaction.DoesNotExist(
                    "Requested session was not found for charger"
                )
            if tx_obj.charger and not tx_obj.charger.is_visible_to(user):
                raise ChargerAccessDeniedError("User cannot access this charger")
            past_session = True
        else:
            tx_obj = Transaction.objects.filter(pk=session_id, charger=charger).first()
            if tx_obj is None:
                raise Transaction.DoesNotExist(
                    "Requested session was not found for connector"
                )
            past_session = True
    elif charger.connector_id is not None:
        for session_charger, session_tx in sessions:
            if session_charger.pk == charger.pk:
                tx_obj = session_tx
                break

    chart_data: dict[str, Any] = {"labels": [], "datasets": []}

    if tx_obj and (charger.connector_id is not None or past_session):
        series_points = _series_from_transaction(tx_obj)
        if series_points:
            chart_data["labels"] = [ts for ts, _ in series_points]
            charger_ref = (
                tx_obj.charger
                if tx_obj.charger and tx_obj.charger.connector_id is not None
                else charger
            )
            chart_data["datasets"].append(
                {
                    "label": str(charger_ref.connector_label),
                    "values": [value for _, value in series_points],
                    "connector_id": charger_ref.connector_id,
                }
            )
    elif charger.connector_id is None:
        dataset_points: list[tuple[str, list[tuple[str, float]], int]] = []
        for sibling, sibling_tx in sessions:
            if sibling.connector_id is None or not sibling_tx:
                continue
            points = _series_from_transaction(sibling_tx)
            if not points:
                continue
            dataset_points.append(
                (str(sibling.connector_label), points, sibling.connector_id)
            )
        if dataset_points:
            all_labels: list[str] = sorted(
                {ts for _, points, _ in dataset_points for ts, _ in points}
            )
            chart_data["labels"] = all_labels
            for label, points, connector_id in dataset_points:
                value_map = {ts: val for ts, val in points}
                chart_data["datasets"].append(
                    {
                        "label": label,
                        "values": [value_map.get(ts) for ts in all_labels],
                        "connector_id": connector_id,
                    }
                )

    return chart_data
