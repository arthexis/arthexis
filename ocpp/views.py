import asyncio
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.http import JsonResponse, HttpResponse, Http404
from django.http.request import split_domain_port
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.utils.translation import gettext_lazy as _
from django.urls import NoReverseMatch, reverse
from django.conf import settings
from django.utils import translation

from utils.api import api_login_required

from pages.utils import landing
from core.liveupdate import live_update

import requests

from . import store
from .models import Transaction, Charger
from .evcs import (
    _start_simulator,
    _stop_simulator,
    get_simulator_state,
    _simulator_status_json,
)


def _normalize_connector_slug(slug: str | None) -> tuple[int | None, str]:
    """Return connector value and normalized slug or raise 404."""

    try:
        value = Charger.connector_value_from_slug(slug)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise Http404("Invalid connector") from exc
    return value, Charger.connector_slug_from_value(value)


def _reverse_connector_url(name: str, serial: str, connector_slug: str) -> str:
    """Return URL name for connector-aware routes."""

    target = f"{name}-connector"
    if connector_slug == Charger.AGGREGATE_CONNECTOR_SLUG:
        try:
            return reverse(target, args=[serial, connector_slug])
        except NoReverseMatch:
            return reverse(name, args=[serial])
    return reverse(target, args=[serial, connector_slug])


def _get_charger(serial: str, connector_slug: str | None) -> tuple[Charger, str]:
    """Return charger for the requested identity, creating if necessary."""

    connector_value, normalized_slug = _normalize_connector_slug(connector_slug)
    if connector_value is None:
        charger, _ = Charger.objects.get_or_create(
            charger_id=serial,
            connector_id=None,
        )
    else:
        charger, _ = Charger.objects.get_or_create(
            charger_id=serial,
            connector_id=connector_value,
        )
    return charger, normalized_slug


def _connector_set(charger: Charger) -> list[Charger]:
    """Return chargers sharing the same serial ordered for navigation."""

    siblings = list(Charger.objects.filter(charger_id=charger.charger_id))
    siblings.sort(key=lambda c: (c.connector_id is not None, c.connector_id or 0))
    return siblings


def _connector_overview(charger: Charger) -> list[dict]:
    """Return connector metadata used for navigation and summaries."""

    overview: list[dict] = []
    for sibling in _connector_set(charger):
        tx_obj = store.get_transaction(sibling.charger_id, sibling.connector_id)
        state, color = _charger_state(sibling, tx_obj)
        overview.append(
            {
                "charger": sibling,
                "slug": sibling.connector_slug,
                "label": sibling.connector_label,
                "url": _reverse_connector_url(
                    "charger-page", sibling.charger_id, sibling.connector_slug
                ),
                "status": state,
                "color": color,
                "tx": tx_obj,
                "connected": store.is_connected(
                    sibling.charger_id, sibling.connector_id
                ),
            }
        )
    return overview


def _live_sessions(charger: Charger) -> list[tuple[Charger, Transaction]]:
    """Return active sessions grouped by connector for the charger."""

    siblings = _connector_set(charger)
    ordered = [c for c in siblings if c.connector_id is not None] + [
        c for c in siblings if c.connector_id is None
    ]
    sessions: list[tuple[Charger, Transaction]] = []
    seen: set[int] = set()
    for sibling in ordered:
        tx_obj = store.get_transaction(sibling.charger_id, sibling.connector_id)
        if not tx_obj:
            continue
        if tx_obj.pk and tx_obj.pk in seen:
            continue
        if tx_obj.pk:
            seen.add(tx_obj.pk)
        sessions.append((sibling, tx_obj))
    return sessions


def _charger_state(charger: Charger, tx_obj: Transaction | None):
    """Return human readable state and color for a charger."""
    cid = charger.charger_id
    connected = store.is_connected(cid, charger.connector_id)
    if connected and tx_obj:
        return _("Charging"), "green"
    if connected:
        return _("Available"), "blue"
    return _("Offline"), "grey"


@api_login_required
def charger_list(request):
    """Return a JSON list of known chargers and state."""
    data = []
    for charger in Charger.objects.all():
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
            if tx_obj.vin:
                tx_data["vin"] = tx_obj.vin
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
            if session_tx.vin:
                active_payload["vin"] = session_tx.vin
            if session_tx.meter_stop is not None:
                active_payload["meterStop"] = session_tx.meter_stop
            if session_tx.stop_time is not None:
                active_payload["stopTime"] = session_tx.stop_time.isoformat()
            active_transactions.append(active_payload)
        data.append(
            {
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
                "connected": store.is_connected(cid, charger.connector_id),
            }
        )
    return JsonResponse({"chargers": data})


@api_login_required
def charger_detail(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)

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
        if tx_obj.vin:
            tx_data["vin"] = tx_obj.vin
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
        if session_tx.vin:
            payload["vin"] = session_tx.vin
        if session_tx.meter_stop is not None:
            payload["meterStop"] = session_tx.meter_stop
        if session_tx.stop_time is not None:
            payload["stopTime"] = session_tx.stop_time.isoformat()
        active_transactions.append(payload)

    log_key = store.identity_key(cid, charger.connector_id)
    log = store.get_logs(log_key, log_type="charger")
    return JsonResponse(
        {
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
            "log": log,
        }
    )


@login_required
@landing("Dashboard")
@live_update()
def dashboard(request):
    """Landing page listing all known chargers and their status."""
    chargers = []
    for charger in Charger.objects.all():
        tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
        if not tx_obj:
            tx_obj = (
                Transaction.objects.filter(charger=charger)
                .order_by("-start_time")
                .first()
            )
        state, color = _charger_state(charger, tx_obj)
        chargers.append({"charger": charger, "state": state, "color": color})
    return render(request, "ocpp/dashboard.html", {"chargers": chargers})


@login_required
@landing("CP Simulator")
@live_update()
def cp_simulator(request):
    """Public landing page to control the OCPP charge point simulator."""
    host_header = request.get_host()
    default_host, host_port = split_domain_port(host_header)
    if not default_host:
        default_host = "127.0.0.1"
    default_ws_port = request.get_port() or host_port or "8000"
    default_cp_paths = ["CP1", "CP2"]
    default_serial_numbers = default_cp_paths
    default_connector_id = 1
    default_rfid = "FFFFFFFF"
    default_vins = ["WP0ZZZ00000000000", "WAUZZZ00000000000"]

    message = ""
    if request.method == "POST":
        cp_idx = int(request.POST.get("cp") or 1)
        action = request.POST.get("action")
        if action == "start":
            sim_params = dict(
                host=request.POST.get("host") or default_host,
                ws_port=int(request.POST.get("ws_port") or default_ws_port),
                cp_path=request.POST.get("cp_path") or default_cp_paths[cp_idx - 1],
                serial_number=request.POST.get("serial_number")
                or default_serial_numbers[cp_idx - 1],
                connector_id=int(
                    request.POST.get("connector_id") or default_connector_id
                ),
                rfid=request.POST.get("rfid") or default_rfid,
                vin=request.POST.get("vin") or default_vins[cp_idx - 1],
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
        "default_serial_numbers": default_serial_numbers,
        "default_connector_id": default_connector_id,
        "default_rfid": default_rfid,
        "default_vins": default_vins,
        "params_jsons": params_jsons,
        "state_jsons": state_jsons,
    }
    return render(request, "ocpp/cp_simulator.html", context)


def charger_page(request, cid, connector=None):
    """Public landing page for a charger displaying usage guidance or progress."""
    charger, connector_slug = _get_charger(cid, connector)
    overview = _connector_overview(charger)
    sessions = _live_sessions(charger)
    tx = None
    active_connector_count = 0
    if charger.connector_id is None:
        if sessions:
            total_kw = 0.0
            start_times = [
                tx_obj.start_time for _, tx_obj in sessions if tx_obj.start_time
            ]
            for _, tx_obj in sessions:
                if tx_obj.kw:
                    total_kw += tx_obj.kw
            tx = SimpleNamespace(kw=total_kw, start_time=min(start_times) if start_times else None)
            active_connector_count = len(sessions)
    else:
        tx = sessions[0][1] if sessions else store.get_transaction(cid, charger.connector_id)
        if tx:
            active_connector_count = 1
    language_cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    preferred_language = "es"
    supported_languages = {code for code, _ in settings.LANGUAGES}
    if (
        preferred_language in supported_languages
        and not language_cookie
    ):
        translation.activate(preferred_language)
        request.LANGUAGE_CODE = translation.get_language()
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": item["url"],
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    connector_overview = [
        item for item in overview if item["charger"].connector_id is not None
    ]
    status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    return render(
        request,
        "ocpp/charger_page.html",
        {
            "charger": charger,
            "tx": tx,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "connector_overview": connector_overview,
            "active_connector_count": active_connector_count,
            "status_url": status_url,
        },
    )


@login_required
def charger_status(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    session_id = request.GET.get("session")
    sessions = _live_sessions(charger)
    live_tx = None
    if charger.connector_id is not None and sessions:
        live_tx = sessions[0][1]
    tx_obj = live_tx
    past_session = False
    if session_id:
        if charger.connector_id is None:
            tx_obj = get_object_or_404(
                Transaction, pk=session_id, charger__charger_id=cid
            )
            past_session = True
        elif not (live_tx and str(live_tx.pk) == session_id):
            tx_obj = get_object_or_404(Transaction, pk=session_id, charger=charger)
            past_session = True
    state, color = _charger_state(charger, live_tx if charger.connector_id is not None else (sessions if sessions else None))
    if charger.connector_id is None:
        transactions_qs = Transaction.objects.filter(
            charger__charger_id=cid
        ).select_related("charger").order_by("-start_time")
    else:
        transactions_qs = Transaction.objects.filter(charger=charger).order_by(
            "-start_time"
        )
    paginator = Paginator(transactions_qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))
    transactions = page_obj.object_list
    chart_data = {"labels": [], "datasets": []}

    def _series_from_transaction(tx):
        points: list[tuple[str, float]] = []
        readings = list(
            tx.meter_values.filter(energy__isnull=False).order_by("timestamp")
        )
        start_val = None
        if tx.meter_start is not None:
            start_val = float(tx.meter_start) / 1000.0
        for reading in readings:
            try:
                val = float(reading.energy)
            except (TypeError, ValueError):
                continue
            if start_val is None:
                start_val = val
            total = val - start_val
            points.append((reading.timestamp.isoformat(), max(total, 0.0)))
        return points

    if tx_obj and (charger.connector_id is not None or past_session):
        series_points = _series_from_transaction(tx_obj)
        if series_points:
            chart_data["labels"] = [ts for ts, _ in series_points]
            connector_id = None
            if tx_obj.charger and tx_obj.charger.connector_id is not None:
                connector_id = tx_obj.charger.connector_id
            elif charger.connector_id is not None:
                connector_id = charger.connector_id
            chart_data["datasets"].append(
                {
                    "label": str(
                        tx_obj.charger.connector_label
                        if tx_obj.charger and tx_obj.charger.connector_id is not None
                        else charger.connector_label
                    ),
                    "values": [value for _, value in series_points],
                    "connector_id": connector_id,
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
    overview = _connector_overview(charger)
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": _reverse_connector_url("charger-status", cid, item["slug"]),
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    connector_overview = [
        item for item in overview if item["charger"].connector_id is not None
    ]
    search_url = _reverse_connector_url("charger-session-search", cid, connector_slug)
    console_url = None
    if charger.console_url:
        console_url = _reverse_connector_url("charger-console", cid, connector_slug)
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
            "chart_data": chart_data,
            "past_session": past_session,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "connector_overview": connector_overview,
            "search_url": search_url,
            "console_url": console_url,
            "show_chart": bool(
                chart_data["datasets"]
                and any(
                    any(value is not None for value in dataset["values"])
                    for dataset in chart_data["datasets"]
                )
            ),
        },
    )


@login_required
def charger_session_search(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    date_str = request.GET.get("date")
    transactions = None
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            start = datetime.combine(
                date_obj, datetime.min.time(), tzinfo=dt_timezone.utc
            )
            end = start + timedelta(days=1)
            qs = Transaction.objects.filter(start_time__gte=start, start_time__lt=end)
            if charger.connector_id is None:
                qs = qs.filter(charger__charger_id=cid)
            else:
                qs = qs.filter(charger=charger)
            transactions = qs.order_by("-start_time")
        except ValueError:
            transactions = []
    overview = _connector_overview(charger)
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": _reverse_connector_url("charger-session-search", cid, item["slug"]),
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    return render(
        request,
        "ocpp/charger_session_search.html",
        {
            "charger": charger,
            "transactions": transactions,
            "date": date_str,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "status_url": status_url,
        },
    )


@login_required
def charger_log_page(request, cid, connector=None):
    """Render a simple page with the log for the charger or simulator."""
    log_type = request.GET.get("type", "charger")
    connector_links = []
    connector_slug = None
    status_url = None
    if log_type == "charger":
        charger, connector_slug = _get_charger(cid, connector)
        log_key = store.identity_key(cid, charger.connector_id)
        overview = _connector_overview(charger)
        connector_links = [
            {
                "slug": item["slug"],
                "label": item["label"],
                "url": _reverse_connector_url("charger-log", cid, item["slug"]),
                "active": item["slug"] == connector_slug,
            }
            for item in overview
        ]
        target_id = log_key
        status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    else:
        charger = Charger.objects.filter(charger_id=cid).first() or Charger(
            charger_id=cid
        )
        target_id = cid
    log = store.get_logs(target_id, log_type=log_type)
    return render(
        request,
        "ocpp/charger_logs.html",
        {
            "charger": charger,
            "log": log,
            "log_type": log_type,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "status_url": status_url,
        },
    )


@csrf_exempt
@api_login_required
def dispatch_action(request, cid, connector=None):
    connector_value, _ = _normalize_connector_slug(connector)
    ws = store.get_connection(cid, connector_value)
    if ws is None:
        return JsonResponse({"detail": "no connection"}, status=404)
    try:
        data = json.loads(request.body.decode()) if request.body else {}
    except json.JSONDecodeError:
        data = {}
    action = data.get("action")
    if action == "remote_stop":
        tx_obj = store.get_transaction(cid, connector_value)
        if not tx_obj:
            return JsonResponse({"detail": "no transaction"}, status=404)
        msg = json.dumps(
            [
                2,
                str(datetime.utcnow().timestamp()),
                "RemoteStopTransaction",
                {"transactionId": tx_obj.pk},
            ]
        )
        asyncio.get_event_loop().create_task(ws.send(msg))
    elif action == "reset":
        msg = json.dumps(
            [2, str(datetime.utcnow().timestamp()), "Reset", {"type": "Soft"}]
        )
        asyncio.get_event_loop().create_task(ws.send(msg))
    else:
        return JsonResponse({"detail": "unknown action"}, status=400)
    log_key = store.identity_key(cid, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    return JsonResponse({"sent": msg})


@login_required
def charger_console(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    if not charger.console_url:
        raise Http404
    overview = _connector_overview(charger)
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": _reverse_connector_url("charger-console", cid, item["slug"]),
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    proxy_url = _reverse_connector_url("charger-console-proxy", cid, connector_slug)
    status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    return render(
        request,
        "ocpp/charger_console.html",
        {
            "charger": charger,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "proxy_url": proxy_url,
            "status_url": status_url,
        },
    )


@login_required
def charger_console_proxy(request, cid, connector=None, path=""):
    charger, connector_slug = _get_charger(cid, connector)
    if not charger.console_url:
        raise Http404
    base = charger.console_url.rstrip("/")
    target = f"{base}/{path}" if path else base
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    try:
        resp = requests.request(
            request.method,
            target,
            params=request.GET,
            data=request.body if request.method != "GET" else None,
            headers=headers,
            cookies=request.COOKIES,
            allow_redirects=False,
            timeout=5,
        )
    except Exception:
        return HttpResponse("", status=502)

    content_type = resp.headers.get("Content-Type", "")
    if content_type.startswith("text/html"):
        proxy_base = _reverse_connector_url("charger-console-proxy", cid, connector_slug)
        text = resp.text.replace('href="/', f'href="{proxy_base}/')
        text = text.replace('src="/', f'src="{proxy_base}/')
        proxy_resp = HttpResponse(text, status=resp.status_code)
    else:
        proxy_resp = HttpResponse(resp.content, status=resp.status_code)
    proxy_resp["Content-Type"] = content_type
    for key, value in resp.headers.items():
        lk = key.lower()
        if lk in (
            "content-length",
            "transfer-encoding",
            "content-encoding",
            "connection",
        ):
            continue
        if key == "Content-Type":
            continue
        proxy_resp[key] = value
    return proxy_resp
