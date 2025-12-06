from django.http import JsonResponse

from utils.api import api_login_required

from .. import store
from ..models import Charger, Transaction
from .common import (
    _charger_state,
    _clear_stale_statuses_for_view,
    _diagnostics_payload,
    _ensure_charger_access,
    _get_charger,
    _live_sessions,
    _visible_chargers,
)

@api_login_required
def charger_list(request):
    """Return a JSON list of known chargers and state."""

    _clear_stale_statuses_for_view()
    data = []
    for charger in _visible_chargers(request.user):
        cid = charger.charger_id
        sessions: list[tuple[Charger, Transaction]] = []
        tx_obj = store.get_transaction(cid, charger.connector_id)
        if charger.connector_id is None:
            sessions = _live_sessions(charger)
            if sessions:
                tx_obj = sessions[0][1]
        elif tx_obj:
            sessions = [(charger, tx_obj)]
        if not tx_obj:
            tx_obj = (
                Transaction.objects.filter(charger__charger_id=cid)
                .order_by("-start_time")
                .first()
            )
        tx_data = None
        if tx_obj:
            tx_data = {
                "transactionId": tx_obj.pk,
                "meterStart": tx_obj.meter_start,
                "startTime": tx_obj.start_time.isoformat(),
            }
            identifier = str(getattr(tx_obj, "vehicle_identifier", "") or "").strip()
            if identifier:
                tx_data["vid"] = identifier
            legacy_vin = str(getattr(tx_obj, "vin", "") or "").strip()
            if legacy_vin:
                tx_data["vin"] = legacy_vin
            if tx_obj.meter_stop is not None:
                tx_data["meterStop"] = tx_obj.meter_stop
            if tx_obj.stop_time is not None:
                tx_data["stopTime"] = tx_obj.stop_time.isoformat()
        active_transactions = []
        for session_charger, session_tx in sessions:
            active_payload = {
                "charger_id": session_charger.charger_id,
                "connector_id": session_charger.connector_id,
                "connector_slug": session_charger.connector_slug,
                "transactionId": session_tx.pk,
                "meterStart": session_tx.meter_start,
                "startTime": session_tx.start_time.isoformat(),
            }
            identifier = str(getattr(session_tx, "vehicle_identifier", "") or "").strip()
            if identifier:
                active_payload["vid"] = identifier
            legacy_vin = str(getattr(session_tx, "vin", "") or "").strip()
            if legacy_vin:
                active_payload["vin"] = legacy_vin
            if session_tx.meter_stop is not None:
                active_payload["meterStop"] = session_tx.meter_stop
            if session_tx.stop_time is not None:
                active_payload["stopTime"] = session_tx.stop_time.isoformat()
            active_transactions.append(active_payload)
        state, color = _charger_state(
            charger,
            tx_obj if charger.connector_id is not None else (sessions if sessions else None),
        )
        entry = {
            "charger_id": cid,
            "name": charger.name,
            "connector_id": charger.connector_id,
            "connector_slug": charger.connector_slug,
            "connector_label": charger.connector_label,
            "require_rfid": charger.require_rfid,
            "transaction": tx_data,
            "activeTransactions": active_transactions,
            "lastHeartbeat": (
                charger.last_heartbeat.isoformat()
                if charger.last_heartbeat
                else None
            ),
            "lastMeterValues": charger.last_meter_values,
            "firmwareStatus": charger.firmware_status,
            "firmwareStatusInfo": charger.firmware_status_info,
            "firmwareTimestamp": (
                charger.firmware_timestamp.isoformat()
                if charger.firmware_timestamp
                else None
            ),
            "connected": store.is_connected(cid, charger.connector_id),
            "lastStatus": charger.last_status or None,
            "lastErrorCode": charger.last_error_code or None,
            "lastStatusTimestamp": (
                charger.last_status_timestamp.isoformat()
                if charger.last_status_timestamp
                else None
            ),
            "lastStatusVendorInfo": charger.last_status_vendor_info,
            "status": state,
            "statusColor": color,
        }
        entry.update(_diagnostics_payload(charger))
        data.append(entry)
    return JsonResponse({"chargers": data})


@api_login_required
def charger_detail(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(
        request.user, charger, request=request
    )
    if access_response is not None:
        return access_response

    sessions: list[tuple[Charger, Transaction]] = []
    tx_obj = store.get_transaction(cid, charger.connector_id)
    if charger.connector_id is None:
        sessions = _live_sessions(charger)
        if sessions:
            tx_obj = sessions[0][1]
    elif tx_obj:
        sessions = [(charger, tx_obj)]
    if not tx_obj:
        tx_obj = (
            Transaction.objects.filter(charger__charger_id=cid)
            .order_by("-start_time")
            .first()
        )

    tx_data = None
    if tx_obj:
        tx_data = {
            "transactionId": tx_obj.pk,
            "meterStart": tx_obj.meter_start,
            "startTime": tx_obj.start_time.isoformat(),
        }
        identifier = str(getattr(tx_obj, "vehicle_identifier", "") or "").strip()
        if identifier:
            tx_data["vid"] = identifier
        legacy_vin = str(getattr(tx_obj, "vin", "") or "").strip()
        if legacy_vin:
            tx_data["vin"] = legacy_vin
        if tx_obj.meter_stop is not None:
            tx_data["meterStop"] = tx_obj.meter_stop
        if tx_obj.stop_time is not None:
            tx_data["stopTime"] = tx_obj.stop_time.isoformat()

    active_transactions = []
    for session_charger, session_tx in sessions:
        payload = {
            "charger_id": session_charger.charger_id,
            "connector_id": session_charger.connector_id,
            "connector_slug": session_charger.connector_slug,
            "transactionId": session_tx.pk,
            "meterStart": session_tx.meter_start,
            "startTime": session_tx.start_time.isoformat(),
        }
        identifier = str(getattr(session_tx, "vehicle_identifier", "") or "").strip()
        if identifier:
            payload["vid"] = identifier
        legacy_vin = str(getattr(session_tx, "vin", "") or "").strip()
        if legacy_vin:
            payload["vin"] = legacy_vin
        if session_tx.meter_stop is not None:
            payload["meterStop"] = session_tx.meter_stop
        if session_tx.stop_time is not None:
            payload["stopTime"] = session_tx.stop_time.isoformat()
        active_transactions.append(payload)

    log_key = store.identity_key(cid, charger.connector_id)
    log = store.get_logs(log_key, log_type="charger")
    state, color = _charger_state(
        charger,
        tx_obj if charger.connector_id is not None else (sessions if sessions else None),
    )
    payload = {
        "charger_id": cid,
        "connector_id": charger.connector_id,
        "connector_slug": connector_slug,
        "name": charger.name,
        "require_rfid": charger.require_rfid,
        "transaction": tx_data,
        "activeTransactions": active_transactions,
        "lastHeartbeat": (
            charger.last_heartbeat.isoformat() if charger.last_heartbeat else None
        ),
        "lastMeterValues": charger.last_meter_values,
        "firmwareStatus": charger.firmware_status,
        "firmwareStatusInfo": charger.firmware_status_info,
        "firmwareTimestamp": (
            charger.firmware_timestamp.isoformat()
            if charger.firmware_timestamp
            else None
        ),
        "log": log,
        "lastStatus": charger.last_status or None,
        "lastErrorCode": charger.last_error_code or None,
        "lastStatusTimestamp": (
            charger.last_status_timestamp.isoformat()
            if charger.last_status_timestamp
            else None
        ),
        "lastStatusVendorInfo": charger.last_status_vendor_info,
        "status": state,
        "statusColor": color,
    }
    payload.update(_diagnostics_payload(charger))
    return JsonResponse(payload)
