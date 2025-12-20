from django.utils.translation import gettext_lazy as _

from ..utils import resolve_ws_scheme

from .common import *  # noqa: F401,F403
from ..evcs import _start_simulator, _stop_simulator


@login_required(login_url="pages:login")
@landing("Charge Point Simulator")
def cp_simulator(request):
    """Public landing page to control the OCPP charge point simulator."""

    ws_scheme = resolve_ws_scheme(request=request)

    def _simulator_target_url(params: dict[str, object]) -> str:
        cp_path = str(params.get("cp_path") or "")
        host = str(params.get("host") or "")
        ws_port = params.get("ws_port")
        if ws_port:
            return f"{ws_scheme}://{host}:{ws_port}/{cp_path}"
        return f"{ws_scheme}://{host}/{cp_path}"

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
            "pre_charge_delay": _cast_value(
                request.POST.get("pre_charge_delay"), float, default_params["pre_charge_delay"]
            ),
            "average_kwh": _cast_value(
                request.POST.get("average_kwh"), float, default_params["average_kwh"]
            ),
            "amperage": _cast_value(
                request.POST.get("amperage"), float, default_params["amperage"]
            ),
            "repeat": bool(repeat_value),
            "username": request.POST.get("username", ""),
            "password": request.POST.get("password", ""),
            "ws_scheme": ws_scheme,
        }
        simulator_slot = _cast_value(
            request.POST.get("simulator_slot"), int, simulator_slot
        )
        if simulator_slot not in {1, 2}:
            simulator_slot = 1
        action = request.POST.get("action")
        if action == "stop":
            _stop_simulator(simulator_slot)
            message = _("Simulator stop requested")
        else:
            name = request.POST.get("simulator_name") or "Simulator"
            delay_value = request.POST.get("start_delay")
            delay = _cast_value(delay_value, float, 0.0)
            sim_params["name"] = name
            sim_params["delay"] = delay
            sim_params["reconnect_slots"] = request.POST.get("reconnect_slots")
            sim_params["demo_mode"] = bool(request.POST.get("demo_mode"))
            sim_params["meter_interval"] = _cast_value(
                request.POST.get("meter_interval"), float, default_params["interval"]
            )
            _start_simulator(sim_params, cp=simulator_slot)
            sim_details = {key: value for key, value in sim_params.items() if key != "password"}
            NetMessage.broadcast(subject="simulator", body=json.dumps(sim_details))
            message = _("Simulator start requested")
            if sim_params["demo_mode"]:
                dashboard_link = reverse("ocpp:ocpp-dashboard")
            if sim_params.get("delay"):
                _broadcast_simulator_started(name, sim_params.get("delay"), sim_params)
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
