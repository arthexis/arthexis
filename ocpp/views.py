import asyncio
import json
from datetime import datetime

from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render

from . import store


def charger_list(request):
    """Return a JSON list of known chargers and state."""
    data = []
    for cid, tx in store.transactions.items():
        data.append({
            "charger_id": cid,
            "transaction": tx,
            "connected": cid in store.connections,
        })
    return JsonResponse({"chargers": data})


def charger_detail(request, cid):
    tx = store.transactions.get(cid)
    log = store.logs.get(cid, [])
    return JsonResponse({"transaction": tx, "log": log})


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
        tx = store.transactions.get(cid)
        if not tx:
            return JsonResponse({"detail": "no transaction"}, status=404)
        msg = json.dumps([2, str(datetime.utcnow().timestamp()), "RemoteStopTransaction", {"transactionId": tx["transactionId"]}])
        asyncio.get_event_loop().create_task(ws.send(msg))
    elif action == "reset":
        msg = json.dumps([2, str(datetime.utcnow().timestamp()), "Reset", {"type": "Soft"}])
        asyncio.get_event_loop().create_task(ws.send(msg))
    else:
        return JsonResponse({"detail": "unknown action"}, status=400)
    store.logs.setdefault(cid, []).append(f"< {msg}")
    return JsonResponse({"sent": msg})
