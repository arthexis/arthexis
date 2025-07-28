import asyncio
import json
from datetime import datetime

from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render

from . import store
from .models import Transaction


def charger_list(request):
    """Return a JSON list of known chargers and state."""
    data = []
    for cid, tx_obj in store.transactions.items():
        tx_data = None
        if tx_obj:
            tx_data = {
                "transactionId": tx_obj.transaction_id,
                "meterStart": tx_obj.meter_start,
                "startTime": tx_obj.start_time.isoformat(),
            }
            if tx_obj.meter_stop is not None:
                tx_data["meterStop"] = tx_obj.meter_stop
            if tx_obj.stop_time is not None:
                tx_data["stopTime"] = tx_obj.stop_time.isoformat()
        data.append({
            "charger_id": cid,
            "transaction": tx_data,
            "connected": cid in store.connections,
        })
    return JsonResponse({"chargers": data})


def charger_detail(request, cid):
    tx_obj = store.transactions.get(cid)
    tx_data = None
    if tx_obj:
        tx_data = {
            "transactionId": tx_obj.transaction_id,
            "meterStart": tx_obj.meter_start,
            "startTime": tx_obj.start_time.isoformat(),
        }
        if tx_obj.meter_stop is not None:
            tx_data["meterStop"] = tx_obj.meter_stop
        if tx_obj.stop_time is not None:
            tx_data["stopTime"] = tx_obj.stop_time.isoformat()
    else:
        tx_obj = (
            Transaction.objects.filter(charger_id=cid)
            .order_by("-start_time")
            .first()
        )
        if tx_obj:
            tx_data = {
                "transactionId": tx_obj.transaction_id,
                "meterStart": tx_obj.meter_start,
                "startTime": tx_obj.start_time.isoformat(),
                "meterStop": tx_obj.meter_stop,
                "stopTime": tx_obj.stop_time.isoformat() if tx_obj.stop_time else None,
            }
    log = store.logs.get(cid, [])
    return JsonResponse({"transaction": tx_data, "log": log})


@csrf_exempt
def dispatch_action(request, cid):
    ws = store.connections.get(cid)
    if ws is None:
        return JsonResponse({"detail": "no connection"}, status=404)
    try:
        data = json.loads(request.body.decode()) if request.body else {}
    except json.JSONDecodeError:
        data = {}
    action = data.get("action")
    if action == "remote_stop":
        tx_obj = store.transactions.get(cid)
        if not tx_obj:
            return JsonResponse({"detail": "no transaction"}, status=404)
        msg = json.dumps([
            2,
            str(datetime.utcnow().timestamp()),
            "RemoteStopTransaction",
            {"transactionId": tx_obj.transaction_id},
        ])
        asyncio.get_event_loop().create_task(ws.send(msg))
    elif action == "reset":
        msg = json.dumps([2, str(datetime.utcnow().timestamp()), "Reset", {"type": "Soft"}])
        asyncio.get_event_loop().create_task(ws.send(msg))
    else:
        return JsonResponse({"detail": "unknown action"}, status=400)
    store.logs.setdefault(cid, []).append(f"< {msg}")
    return JsonResponse({"sent": msg})
