import asyncio
import json
from datetime import datetime

from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator

from utils.api import api_login_required

from website.utils import landing

from . import store
from .models import Transaction, Charger
from .evcs import (
    _start_simulator,
    _stop_simulator,
    get_simulator_state,
    _simulator_status_json,
)


def _charger_state(charger: Charger, tx_obj: Transaction | None):
    """Return human readable state and color for a charger."""
    cid = charger.charger_id
    connected = cid in store.connections
    if connected and tx_obj:
        return "Charging", "green"
    if connected:
        return "Available", "blue"
    return "Offline", "grey"



@api_login_required
def charger_list(request):
    """Return a JSON list of known chargers and state."""
    data = []
    for charger in Charger.objects.all():
        cid = charger.charger_id
        tx_obj = store.transactions.get(cid)
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
            if tx_obj.meter_stop is not None:
                tx_data["meterStop"] = tx_obj.meter_stop
            if tx_obj.stop_time is not None:
                tx_data["stopTime"] = tx_obj.stop_time.isoformat()
        data.append(
            {
                "charger_id": cid,
                "name": charger.name,
                "config": charger.config,
                "require_rfid": charger.require_rfid,
                "transaction": tx_data,
                "lastHeartbeat": charger.last_heartbeat.isoformat() if charger.last_heartbeat else None,
                "lastMeterValues": charger.last_meter_values,
                "connected": cid in store.connections,
            }
        )
    return JsonResponse({"chargers": data})


@api_login_required
def charger_detail(request, cid):
    charger = Charger.objects.filter(charger_id=cid).first()
    if charger is None:
        return JsonResponse({"detail": "not found"}, status=404)

    tx_obj = store.transactions.get(cid)
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
        if tx_obj.meter_stop is not None:
            tx_data["meterStop"] = tx_obj.meter_stop
        if tx_obj.stop_time is not None:
            tx_data["stopTime"] = tx_obj.stop_time.isoformat()

    log = store.get_logs(cid, log_type="charger")
    return JsonResponse(
        {
            "charger_id": cid,
            "name": charger.name,
            "config": charger.config,
            "require_rfid": charger.require_rfid,
            "transaction": tx_data,
            "lastHeartbeat": charger.last_heartbeat.isoformat() if charger.last_heartbeat else None,
            "lastMeterValues": charger.last_meter_values,
            "log": log,
        }
    )


@landing("Dashboard")
def dashboard(request):
    """Landing page listing all known chargers and their status."""
    chargers = []
    for charger in Charger.objects.all():
        tx_obj = store.transactions.get(charger.charger_id)
        if not tx_obj:
            tx_obj = (
                Transaction.objects.filter(charger=charger)
                .order_by("-start_time")
                .first()
            )
        state, color = _charger_state(charger, tx_obj)
        chargers.append({"charger": charger, "state": state, "color": color})
    return render(request, "ocpp/dashboard.html", {"chargers": chargers})


@landing("Simulator")
def cp_simulator(request):
    """Public landing page to control the OCPP charge point simulator."""
    default_host = "127.0.0.1"
    default_ws_port = "9000"
    default_cp_paths = ["CP1", "CP2"]
    default_rfid = "FFFFFFFF"

    message = ""
    if request.method == "POST":
        cp_idx = int(request.POST.get("cp") or 1)
        action = request.POST.get("action")
        if action == "start":
            sim_params = dict(
                host=request.POST.get("host") or default_host,
                ws_port=int(request.POST.get("ws_port") or default_ws_port),
                cp_path=request.POST.get("cp_path")
                or default_cp_paths[cp_idx - 1],
                rfid=request.POST.get("rfid") or default_rfid,
                duration=int(request.POST.get("duration") or 600),
                interval=float(request.POST.get("interval") or 5),
                kw_min=float(request.POST.get("kw_min") or 30),
                kw_max=float(request.POST.get("kw_max") or 60),
                pre_charge_delay=float(request.POST.get("pre_charge_delay") or 0),
                repeat=request.POST.get("repeat") or False,
                daemon=True,
                username=request.POST.get("username") or None,
                password=request.POST.get("password") or None,
            )
            try:
                started, status, log_file = _start_simulator(sim_params, cp=cp_idx)
                if started:
                    message = f"CP{cp_idx} started: {status}. Logs: {log_file}"
                else:
                    message = f"CP{cp_idx} {status}. Logs: {log_file}"
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Failed to start CP{cp_idx}: {exc}"
        elif action == "stop":
            try:
                _stop_simulator(cp=cp_idx)
                message = f"CP{cp_idx} stop requested."
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Failed to stop CP{cp_idx}: {exc}"
        else:
            message = "Unknown action."

    states_dict = get_simulator_state()
    state_list = [states_dict[1], states_dict[2]]
    params_jsons = [
        json.dumps(state_list[0].get("params", {}), indent=2),
        json.dumps(state_list[1].get("params", {}), indent=2),
    ]
    state_jsons = [
        _simulator_status_json(1),
        _simulator_status_json(2),
    ]

    context = {
        "message": message,
        "states": state_list,
        "default_host": default_host,
        "default_ws_port": default_ws_port,
        "default_cp_paths": default_cp_paths,
        "default_rfid": default_rfid,
        "params_jsons": params_jsons,
        "state_jsons": state_jsons,
    }
    return render(request, "ocpp/cp_simulator.html", context)


def charger_page(request, cid):
    charger = get_object_or_404(Charger, charger_id=cid)
    tx_obj = store.transactions.get(cid)
    state, color = _charger_state(charger, tx_obj)
    transactions_qs = Transaction.objects.filter(charger=charger).order_by("-start_time")
    paginator = Paginator(transactions_qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))
    transactions = page_obj.object_list
    chart_data = {"labels": [], "values": []}
    if tx_obj:
        total = 0.0
        readings = tx_obj.meter_readings.filter(
            measurand__in=["", "Energy.Active.Import.Register"]
        ).order_by("timestamp")
        for reading in readings:
            try:
                val = float(reading.value)
            except (TypeError, ValueError):
                continue
            if reading.unit == "kW":
                total += val
            else:
                total += val / 1000.0
            chart_data["labels"].append(reading.timestamp.isoformat())
            chart_data["values"].append(total)
    return render(
        request,
        "ocpp/charger_status.html",
        {
            "charger": charger,
            "tx": tx_obj,
            "state": state,
            "color": color,
            "transactions": transactions,
            "page_obj": page_obj,
            "chart_data": json.dumps(chart_data),
        },
    )


def charger_session_search(request, cid):
    charger = get_object_or_404(Charger, charger_id=cid)
    date_str = request.GET.get("date")
    transactions = None
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            transactions = (
                Transaction.objects.filter(charger=charger, start_time__date=date_obj)
                .order_by("-start_time")
            )
        except ValueError:
            transactions = []
    return render(
        request,
        "ocpp/charger_session_search.html",
        {"charger": charger, "transactions": transactions, "date": date_str},
    )


def charger_log_page(request, cid):
    """Render a simple page with the log for the charger or simulator."""
    log_type = request.GET.get("type", "charger")
    try:
        charger = Charger.objects.get(charger_id=cid)
    except Charger.DoesNotExist:
        charger = Charger(charger_id=cid)
    log = store.get_logs(cid, log_type=log_type)
    return render(
        request,
        "ocpp/charger_logs.html",
        {"charger": charger, "log": log},
    )

def charger_status(request, cid):
    """Display current transaction and charger state."""
    return charger_page(request, cid)


@csrf_exempt
@api_login_required
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
            {"transactionId": tx_obj.pk},
        ])
        asyncio.get_event_loop().create_task(ws.send(msg))
    elif action == "reset":
        msg = json.dumps([2, str(datetime.utcnow().timestamp()), "Reset", {"type": "Soft"}])
        asyncio.get_event_loop().create_task(ws.send(msg))
    else:
        return JsonResponse({"detail": "unknown action"}, status=400)
    store.add_log(cid, f"< {msg}", log_type="charger")
    return JsonResponse({"sent": msg})
