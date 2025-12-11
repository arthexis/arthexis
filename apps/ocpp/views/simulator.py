from django.contrib.auth.decorators import login_required
from django.http.request import split_domain_port
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse

from apps.nodes.models import NetMessage
from apps.pages.utils import landing

from ..evcs import _start_simulator, _stop_simulator, get_simulator_state
from ..models import Simulator


@login_required(login_url="pages:login")
@landing("Charge Point Simulator")
def cp_simulator(request):
    """Public landing page to control the OCPP charge point simulator."""

    def _simulator_target_url(params: dict[str, object]) -> str:
        cp_path = str(params.get("cp_path") or "")
        host = str(params.get("host") or "")
        ws_port = params.get("ws_port")
        if ws_port:
            return f"ws://{host}:{ws_port}/{cp_path}"
        return f"ws://{host}/{cp_path}"

    def _broadcast_simulator_started(
        name: str, delay: float | int | None, params: dict[str, object]
    ) -> None:
        delay_value: float | int | None = 0
        if isinstance(delay, (int, float)):
            delay_value = int(delay) if float(delay).is_integer() else delay
        subject = f"{name} {delay_value}s"
        NetMessage.broadcast(subject=subject, body=_simulator_target_url(params))

    simulator_slot = 1
    host_header = request.get_host()
    default_host, host_port = split_domain_port(host_header)
    if not default_host:
        default_host = "127.0.0.1"
    default_ws_port = request.get_port() or host_port or "8000"

    default_simulator = (
        Simulator.objects.filter(default=True, is_deleted=False).order_by("pk").first()
    )
    default_params = {
        "host": default_host,
        "ws_port": int(default_ws_port) if default_ws_port else None,
        "cp_path": "CP2",
        "serial_number": "CP2",
        "connector_id": 1,
        "rfid": "FFFFFFFF",
        "vin": "WP0ZZZ00000000000",
        "duration": 600,
        "interval": 5.0,
        "pre_charge_delay": 0.0,
        "average_kwh": 60.0,
        "amperage": 90.0,
        "repeat": False,
        "username": "",
        "password": "",
    }
    if default_simulator:
        default_params.update(
            {
                "host": default_simulator.host or default_host,
                "ws_port": default_simulator.ws_port
                if default_simulator.ws_port is not None
                else default_params["ws_port"],
                "cp_path": default_simulator.cp_path or default_params["cp_path"],
                "serial_number": default_simulator.serial_number
                or default_simulator.cp_path
                or default_params["serial_number"],
                "connector_id": default_simulator.connector_id or 1,
                "rfid": default_simulator.rfid or default_params["rfid"],
                "vin": default_simulator.vin or default_params["vin"],
                "duration": default_simulator.duration or default_params["duration"],
                "interval": default_simulator.interval or default_params["interval"],
                "pre_charge_delay": default_simulator.pre_charge_delay
                if default_simulator.pre_charge_delay is not None
                else default_params["pre_charge_delay"],
                "average_kwh": default_simulator.average_kwh
                or default_params["average_kwh"],
                "amperage": default_simulator.amperage
                or default_params["amperage"],
                "repeat": default_simulator.repeat,
                "username": default_simulator.username or "",
                "password": default_simulator.password or "",
            }
        )

    def _cast_value(value, cast, fallback):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return fallback

    def _port_value(raw_value):
        if raw_value is None:
            return default_params["ws_port"]
        if str(raw_value).strip():
            return _cast_value(raw_value, int, default_params["ws_port"])
        return None

    is_htmx = request.headers.get("HX-Request") == "true"
    message = ""
    dashboard_link: str | None = None
    if request.method == "POST":
        action = request.POST.get("action")
        repeat_value = request.POST.get("repeat")
        sim_params = {
            "host": request.POST.get("host") or default_params["host"],
            "ws_port": _port_value(request.POST.get("ws_port")),
            "cp_path": request.POST.get("cp_path") or default_params["cp_path"],
            "serial_number": request.POST.get("serial_number")
            or default_params["serial_number"],
            "connector_id": _cast_value(
                request.POST.get("connector_id"), int, default_params["connector_id"]
            ),
            "rfid": request.POST.get("rfid") or default_params["rfid"],
            "vin": request.POST.get("vin") or default_params["vin"],
            "duration": _cast_value(
                request.POST.get("duration"), int, default_params["duration"]
            ),
            "interval": _cast_value(
                request.POST.get("interval"), float, default_params["interval"]
            ),
            "average_kwh": _cast_value(
                request.POST.get("average_kwh"),
                float,
                default_params["average_kwh"],
            ),
            "amperage": _cast_value(
                request.POST.get("amperage"), float, default_params["amperage"]
            ),
            "pre_charge_delay": _cast_value(
                request.POST.get("pre_charge_delay"),
                float,
                default_params["pre_charge_delay"],
            ),
            "repeat": default_params["repeat"],
            "daemon": True,
            "username": request.POST.get("username")
            or default_params["username"]
            or None,
            "password": request.POST.get("password")
            or default_params["password"]
            or None,
        }
        if repeat_value is not None:
            sim_params["repeat"] = repeat_value == "True"

        if action == "start":
            try:
                started, status, log_file = _start_simulator(sim_params, cp=simulator_slot)
                prefix = default_simulator.name if default_simulator else "CP Simulator"
                if started:
                    message = f"{prefix} started: {status}. Logs: {log_file}"
                    _broadcast_simulator_started(
                        prefix,
                        sim_params.get("pre_charge_delay"),
                        sim_params,
                    )
                    try:
                        dashboard_link = reverse(
                            "ocpp:charger-status", args=[sim_params["cp_path"]]
                        )
                    except NoReverseMatch:  # pragma: no cover - defensive
                        dashboard_link = None
                else:
                    message = f"{prefix} {status}. Logs: {log_file}"
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Failed to start simulator: {exc}"
        elif action == "stop":
            try:
                _stop_simulator(cp=simulator_slot)
                message = "Simulator stop requested."
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Failed to stop simulator: {exc}"
        else:
            message = "Unknown action."

    refresh_state = is_htmx or request.method == "POST"
    state = get_simulator_state(cp=simulator_slot, refresh_file=refresh_state)
    state_params = state.get("params") or {}

    form_params = {key: state_params.get(key, default_params[key]) for key in default_params}
    form_params["password"] = ""

    if not default_simulator:
        message = message or "No default CP Simulator is configured; using local defaults."

    context = {
        "message": message,
        "dashboard_link": dashboard_link,
        "state": state,
        "form_params": form_params,
        "simulator_slot": simulator_slot,
        "default_simulator": default_simulator,
    }

    template = "ocpp/includes/cp_simulator_panel.html" if is_htmx else "ocpp/cp_simulator.html"
    return render(request, template, context)
