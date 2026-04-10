from datetime import timedelta
import ipaddress

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import resolve_url
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..utils import resolve_ws_scheme
from apps.core.notifications import LcdChannel
from apps.screens.startup_notifications import format_lcd_lines

from .common import *  # noqa: F401,F403
from apps.simulators.evcs import _start_simulator, _stop_simulator, parse_repeat
from apps.simulators.simulator_runtime import (
    ARTHEXIS_BACKEND,
    MOBILITY_HOUSE_BACKEND,
    get_simulator_backend_choices,
)

REPEAT_TRUE_STRINGS = {
    "true",
    "1",
    "yes",
    "on",
    "forever",
    "infinite",
    "loop",
}

@landing("OCPP Simulator")
def cp_simulator(request):
    """Public landing page to control the OCPP charge point simulator."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return redirect_to_login(request.get_full_path(), resolve_url("pages:login"))
    if request.method == "POST" and not getattr(user, "is_staff", False):
        return HttpResponse("Forbidden", status=403)

    ws_scheme = resolve_ws_scheme(request=request)

    def _lcd_simulator_lines(
        name: str, params: dict[str, object], *, status: str = "running", delay: float | int | None = None
    ) -> tuple[str, str]:
        cp_path = str(params.get("cp_path") or "").strip()
        host = str(params.get("host") or "").strip()
        ws_port = params.get("ws_port")
        target = ""
        if host:
            target = f"{host}:{ws_port}" if ws_port else host
        subject = f"SIM {cp_path or name}".strip()
        if status == "delay":
            delay_value: float | int | None = 0
            if isinstance(delay, (int, float)):
                delay_value = int(delay) if float(delay).is_integer() else delay
            body = f"Delay {delay_value}s"
        else:
            body = target or "Running"
        return format_lcd_lines(subject, body)

    def _normalize_repeat(value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in REPEAT_TRUE_STRINGS
        return bool(value)

    def _simulator_expires_at(params: dict[str, object]):
        if parse_repeat(params.get("repeat")) == float("inf"):
            return None
        duration = params.get("duration")
        delay = params.get("delay") or 0
        try:
            duration_value = int(duration) if duration is not None else 0
        except (TypeError, ValueError):
            return None
        try:
            delay_value = max(int(delay), 0)
        except (TypeError, ValueError):
            delay_value = 0
        total_seconds = duration_value + delay_value
        if total_seconds <= 0:
            return None
        return timezone.now() + timedelta(seconds=total_seconds)

    def _broadcast_simulator_started(
        name: str, delay: float | int | None, params: dict[str, object]
    ) -> None:
        delay_value: float | int | None = 0
        if isinstance(delay, (int, float)):
            delay_value = int(delay) if float(delay).is_integer() else delay
        subject, body = _lcd_simulator_lines(
            name, params, status="delay", delay=delay_value
        )
        expires_at = None
        if delay_value:
            expires_at = timezone.now() + timedelta(seconds=float(delay_value))
        NetMessage.broadcast(
            subject=subject,
            body=body,
            expires_at=expires_at,
            lcd_channel_type=LcdChannel.LOW.value,
        )

    simulator_slot = 1
    host_header = request.get_host()
    default_host, host_port = split_domain_port(host_header)
    if not default_host:
        default_host = "127.0.0.1"
    default_ws_port = request.get_port() or host_port or "8000"

    def _format_host_with_port(host: object, ws_port: object) -> str:
        host_value = str(host or "").strip()
        port_value = str(ws_port or "").strip()
        if not host_value:
            return ""
        host_display = (
            host_value
            if ":" not in host_value or (host_value.startswith("[") and host_value.endswith("]"))
            else f"[{host_value}]"
        )
        if not port_value:
            return host_display
        return f"{host_display}:{port_value}"

    default_simulator = (
        Simulator.objects.filter(default=True, is_deleted=False).order_by("pk").first()
    )
    default_params = {
        "host": _format_host_with_port(default_host, default_ws_port),
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
                "host": _format_host_with_port(
                    default_simulator.host or default_host,
                    default_simulator.ws_port
                    if default_simulator.ws_port is not None
                    else default_params["ws_port"],
                ),
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

    def _host_and_port_from_input(raw_host: object) -> tuple[str, int | None]:
        host_input = str(raw_host or "").strip()
        if not host_input:
            return default_host, default_params["ws_port"]

        if host_input.startswith("["):
            closing_bracket = host_input.find("]")
            if closing_bracket > 0:
                bracket_host = host_input[1:closing_bracket].strip()
                remainder = host_input[closing_bracket + 1 :].strip()
                if not remainder:
                    return bracket_host or host_input, None
                if remainder.startswith(":"):
                    port_input = remainder[1:].strip()
                    try:
                        return bracket_host or host_input, int(port_input)
                    except (TypeError, ValueError):
                        return bracket_host or host_input, None
                return bracket_host or host_input, None

        parsed_host, parsed_port = split_domain_port(host_input)
        final_host = parsed_host or host_input

        if parsed_host and parsed_port is not None:
            try:
                return final_host, int(parsed_port)
            except (TypeError, ValueError):
                return final_host, None

        if ":" in host_input and host_input.count(":") >= 2:
            ipv6_host, separator, maybe_port = host_input.rpartition(":")
            if separator and maybe_port:
                try:
                    ipaddress.IPv6Address(ipv6_host)
                    return ipv6_host, int(maybe_port)
                except (ipaddress.AddressValueError, TypeError, ValueError):
                    return final_host, None

        if parsed_port is None:
            return final_host, None
        try:
            return final_host, int(parsed_port)
        except (TypeError, ValueError):
            return final_host, None

    is_htmx = request.headers.get("HX-Request") == "true"
    message = ""
    dashboard_link: str | None = None
    backend_choices = get_simulator_backend_choices()
    backend_values = {value for value, _label in backend_choices}
    preferred_default_backend = (
        MOBILITY_HOUSE_BACKEND
        if MOBILITY_HOUSE_BACKEND in backend_values
        else (ARTHEXIS_BACKEND if ARTHEXIS_BACKEND in backend_values else None)
    )
    session_backend = str(request.session.get("cp_simulator_backend") or "").strip().lower()
    selected_backend = (
        session_backend
        if session_backend in backend_values
        else (preferred_default_backend if preferred_default_backend else "")
    )
    backends_available = bool(backend_choices)
    if request.method == "POST":
        action = request.POST.get("action")
        requested_backend = str(request.POST.get("simulator_backend") or selected_backend).strip().lower()
        if requested_backend not in backend_values:
            requested_backend = selected_backend
        if requested_backend in backend_values:
            selected_backend = requested_backend
            request.session["cp_simulator_backend"] = selected_backend

        if not backends_available:
            message = _("No simulator backends are enabled. Enable one in feature parameters.")
            refresh_state = is_htmx or request.method == "POST"
            state = get_simulator_state(cp=simulator_slot, refresh_file=refresh_state)
            state_params = state.get("params") or {}
            form_params = {key: state_params.get(key, default_params[key]) for key in default_params}
            if "host" in state_params or "ws_port" in state_params:
                form_params["host"] = _format_host_with_port(
                    state_params.get("host", default_host),
                    state_params.get("ws_port"),
                )
            form_params["repeat"] = _normalize_repeat(form_params.get("repeat"))
            form_params["password"] = ""
            context = {
                "message": message,
                "dashboard_link": dashboard_link,
                "state": state,
                "form_params": form_params,
                "simulator_slot": simulator_slot,
                "default_simulator": default_simulator,
                "selected_backend": selected_backend,
                "backend_choices": backend_choices,
                "backends_available": backends_available,
            }
            template = "ocpp/includes/cp_simulator_panel.html" if is_htmx else "ocpp/cp_simulator.html"
            return render(request, template, context)
        repeat_value = _normalize_repeat(request.POST.get("repeat"))
        normalized_host, normalized_port = _host_and_port_from_input(
            request.POST.get("host")
        )
        sim_params = {
            "host": normalized_host,
            "ws_port": normalized_port,
            "cp_path": request.POST.get("cp_path") or default_params["cp_path"],
            "serial_number": request.POST.get("serial_number")
            or request.POST.get("cp_path")
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
            "repeat": repeat_value,
            "username": request.POST.get("username", ""),
            "password": request.POST.get("password", ""),
            "allow_private_network": bool(getattr(user, "is_staff", False)),
            "ws_scheme": ws_scheme,
            "simulator_backend": selected_backend,
        }
        simulator_slot = _cast_value(
            request.POST.get("simulator_slot"), int, simulator_slot
        )
        if simulator_slot not in {1, 2}:
            simulator_slot = 1
        action = request.POST.get("action")
        if action == "select-backend":
            message = _("Simulator backend updated")
        elif action == "stop":
            _stop_simulator(simulator_slot)
            subject, body = format_lcd_lines("SIM STOP", "")
            NetMessage.broadcast(
                subject=subject,
                body=body,
                expires_at=timezone.now() + timedelta(seconds=1),
                lcd_channel_type=LcdChannel.LOW.value,
            )
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
            subject, body = _lcd_simulator_lines(name, sim_params)
            NetMessage.broadcast(
                subject=subject,
                body=body,
                expires_at=_simulator_expires_at(sim_params),
                lcd_channel_type=LcdChannel.LOW.value,
            )
            message = _("Simulator start requested")
            if sim_params["demo_mode"]:
                dashboard_link = reverse("ocpp:ocpp-dashboard")
            if sim_params.get("delay"):
                _broadcast_simulator_started(name, sim_params.get("delay"), sim_params)
    refresh_state = is_htmx or request.method == "POST"
    state = get_simulator_state(cp=simulator_slot, refresh_file=refresh_state)
    state_params = state.get("params") or {}

    form_params = {key: state_params.get(key, default_params[key]) for key in default_params}
    if "host" in state_params or "ws_port" in state_params:
        form_params["host"] = _format_host_with_port(
            state_params.get("host", default_host),
            state_params.get("ws_port"),
        )
    form_params["repeat"] = _normalize_repeat(form_params.get("repeat"))
    form_params["password"] = ""

    context = {
        "message": message,
        "dashboard_link": dashboard_link,
        "state": state,
        "form_params": form_params,
        "simulator_slot": simulator_slot,
        "default_simulator": default_simulator,
        "selected_backend": selected_backend,
        "backend_choices": backend_choices,
        "backends_available": backends_available,
    }

    template = "ocpp/includes/cp_simulator_panel.html" if is_htmx else "ocpp/cp_simulator.html"
    return render(request, template, context)
