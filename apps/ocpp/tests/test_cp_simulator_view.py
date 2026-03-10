import re
import pytest
from django.urls import reverse

from apps.simulators import evcs
from apps.simulators.evcs import _start_simulator, get_simulator_state
from apps.ocpp.views import simulator as simulator_view
from apps.ocpp.models import Simulator


pytestmark = [pytest.mark.django_db, pytest.mark.slow]


@pytest.fixture(autouse=True)
def reset_simulator_state():
    evcs._simulators = {1: evcs.SimulatorState(), 2: evcs.SimulatorState()}
    if evcs.STATE_FILE.exists():
        evcs.STATE_FILE.unlink()
    yield
    if evcs.STATE_FILE.exists():
        evcs.STATE_FILE.unlink()


@pytest.fixture()
def fake_simulate(monkeypatch):
    async def _fake_simulate(
        *,
        host: str = "127.0.0.1",
        ws_port: int | None = 8000,
        rfid: str = "FFFFFFFF",
        cp_path: str = "CPX",
        vin: str = "",
        serial_number: str = "",
        connector_id: int = 1,
        duration: int = 600,
        average_kwh: float = 60.0,
        amperage: float = 90.0,
        pre_charge_delay: float = 0.0,
        repeat: object = False,
        threads: int | None = None,
        daemon: bool = True,
        interval: float = 5.0,
        username: str | None = None,
        password: str | None = None,
        allow_private_network: bool = False,
        ws_scheme: str | None = None,
        use_tls: bool | None = None,
        cp: int = 1,
        name: str = "Simulator",
        delay: float | None = None,
        reconnect_slots: str | None = None,
        demo_mode: bool = False,
        meter_interval: float | None = None,
        **kwargs,
    ):
        state = evcs._simulators[cp]
        state.params = {
            "host": host,
            "ws_port": ws_port,
            "rfid": rfid,
            "cp_path": cp_path,
            "vin": vin,
            "serial_number": serial_number,
            "connector_id": connector_id,
            "duration": duration,
            "average_kwh": average_kwh,
            "amperage": amperage,
            "pre_charge_delay": pre_charge_delay,
            "repeat": repeat,
            "threads": threads,
            "daemon": daemon,
            "interval": interval,
            "username": username,
            "password": password,
            "allow_private_network": allow_private_network,
            "ws_scheme": ws_scheme,
            "use_tls": use_tls,
            "name": name,
            "delay": delay,
            "reconnect_slots": reconnect_slots,
            "demo_mode": demo_mode,
            "meter_interval": meter_interval,
        }
        state.phase = "Available"
        state.last_status = "Connection accepted"
        evcs._save_state_file(evcs._simulators)

    monkeypatch.setattr(evcs, "simulate", _fake_simulate)
    return _fake_simulate


@pytest.fixture(autouse=True)
def silence_broadcasts(monkeypatch):
    monkeypatch.setattr(simulator_view.NetMessage, "broadcast", lambda *_, **__: None)


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="simulator-user", email="sim@example.com", password="pass"
    )
    user.is_staff = True
    user.save()
    client.force_login(user)
    return client


def test_cp_simulator_accepts_parameters_for_both_slots(
    logged_in_client, fake_simulate
):
    slot_one_payload = {
        "simulator_slot": "1",
        "simulator_name": "Alpha",
        "start_delay": "2.5",
        "reconnect_slots": "1,2",
        "demo_mode": "on",
        "meter_interval": "12.5",
        "host": "example.com:9000",
        "cp_path": "CP-ALPHA",
        "serial_number": "SERIAL-ALPHA",
        "connector_id": "2",
        "rfid": "RFIDA",
        "vin": "VINA",
        "duration": "300",
        "interval": "7.5",
        "pre_charge_delay": "1.5",
        "average_kwh": "45.5",
        "amperage": "32.0",
        "repeat": "on",
        "username": "alice",
        "password": "wonder",
    }

    response = logged_in_client.post(reverse("ocpp:cp-simulator"), data=slot_one_payload)

    assert response.status_code == 200
    assert response.context["message"] == "Simulator start requested"

    state = get_simulator_state(cp=1, refresh_file=True)
    params = state["params"]

    assert params["delay"] == 2.5
    assert params["reconnect_slots"] == "1,2"
    assert params["demo_mode"] is True
    assert params["meter_interval"] == 12.5
    assert "username" not in params
    assert "password" not in params
    assert params["host"] == "example.com"
    assert params["ws_port"] == 9000
    assert params["cp_path"] == "CP-ALPHA"
    assert params["serial_number"] == "SERIAL-ALPHA"
    assert params["connector_id"] == 2
    assert params["rfid"] == "RFIDA"
    assert params["vin"] == "VINA"
    assert params["duration"] == 300
    assert params["interval"] == 7.5
    assert params["pre_charge_delay"] == 1.5
    assert params["average_kwh"] == 45.5
    assert params["amperage"] == 32.0
    assert params["repeat"] is True
    assert state["last_status"].endswith("Connection accepted")

    slot_two_payload = {
        **slot_one_payload,
        "simulator_slot": "2",
        "simulator_name": "Beta",
        "cp_path": "CP-BETA",
        "serial_number": "SERIAL-BETA",
        "username": "bob",
        "password": "builder",
        "start_delay": "0.0",
        "reconnect_slots": "2",
        "demo_mode": "",
        "meter_interval": "3.5",
    }

    response = logged_in_client.post(reverse("ocpp:cp-simulator"), data=slot_two_payload)

    assert response.status_code == 200
    assert response.context["message"] == "Simulator start requested"

    state_two = get_simulator_state(cp=2, refresh_file=True)
    params_two = state_two["params"]

    assert params_two["delay"] == 0.0
    assert params_two["reconnect_slots"] == "2"
    assert params_two["demo_mode"] is False
    assert params_two["meter_interval"] == 3.5
    assert "username" not in params_two
    assert "password" not in params_two
    assert params_two["cp_path"] == "CP-BETA"
    assert params_two["serial_number"] == "SERIAL-BETA"
    assert state_two["last_status"].endswith("Connection accepted")


def test_cp_simulator_stop_updates_status(logged_in_client, fake_simulate):
    response = logged_in_client.post(
        reverse("ocpp:cp-simulator"),
        data={"simulator_slot": "1", "action": "stop"},
    )

    assert response.status_code == 200
    assert response.context["message"] == "Simulator stop requested"

    state = get_simulator_state(cp=1, refresh_file=True)
    assert state["last_status"] == "Requested stop (will finish current run)..."
    assert state["running"] is False


def test_start_simulator_ignores_unexpected_params(fake_simulate):
    started, status, _ = _start_simulator({"cp_path": "CP-GAMMA", "foo": "bar"}, cp=1)

    assert started is True
    assert status.endswith("Connection accepted")

    params = get_simulator_state(cp=1, refresh_file=True)["params"]
    assert "foo" not in params
    assert params["cp_path"] == "CP-GAMMA"


def test_cp_simulator_hides_disabled_backends_from_dropdown(logged_in_client, monkeypatch):
    """Backend dropdown should only include runtime-enabled choices."""

    monkeypatch.setattr(
        "apps.ocpp.views.simulator.get_simulator_backend_choices",
        lambda: (("arthexis", "arthexis"),),
    )

    response = logged_in_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'value="arthexis"' in content
    assert 'value="mobilityhouse"' not in content


def test_cp_simulator_defaults_to_mobilityhouse_when_available(logged_in_client, monkeypatch):
    """Default backend should prefer Mobility House when the option is enabled."""

    monkeypatch.setattr(
        "apps.ocpp.views.simulator.get_simulator_backend_choices",
        lambda: (("arthexis", "arthexis"), ("mobilityhouse", "mobilityhouse")),
    )

    response = logged_in_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    assert response.context["selected_backend"] == "mobilityhouse"


def test_cp_simulator_backend_selection_persists_in_session(logged_in_client):
    """Backend dropdown updates the stored backend preference without starting a simulator."""

    response = logged_in_client.post(
        reverse("ocpp:cp-simulator"),
        data={"action": "select-backend", "simulator_backend": "mobilityhouse"},
    )

    assert response.status_code == 200
    assert response.context["message"] == "Simulator backend updated"
    assert response.wsgi_request.session["cp_simulator_backend"] == "mobilityhouse"

    assert get_simulator_state(cp=1, refresh_file=True)["running"] is False
    assert get_simulator_state(cp=2, refresh_file=True)["running"] is False


def test_cp_simulator_form_uses_single_host_input(logged_in_client):
    """Regression: host stays editable while the dedicated port input remains removed."""

    response = logged_in_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    content = response.content.decode()
    host_tag_match = re.search(r'<input[^>]*id="host1"[^>]*>', content)
    assert host_tag_match, "Input with id='host1' not found"
    assert "hx-preserve" in host_tag_match.group(0), "hx-preserve missing from host1 input"

    assert 'id="ws_port1"' not in content
    assert 'id="serial_number1"' in content
    assert 'for="cp_path1">CP Path<' in content
    assert 'Charge Delay (s)' in content
    assert 'How simulation works' in content


def test_cp_simulator_host_without_port_clears_ws_port(logged_in_client, fake_simulate):
    payload = {
        "simulator_slot": "1",
        "host": "example.com",
        "cp_path": "CP-PLAIN",
        "serial_number": "SERIAL-PLAIN",
    }

    response = logged_in_client.post(reverse("ocpp:cp-simulator"), data=payload)

    assert response.status_code == 200
    params = get_simulator_state(cp=1, refresh_file=True)["params"]
    assert params["host"] == "example.com"
    assert params["ws_port"] is None


def test_cp_simulator_accepts_ipv6_host_port(logged_in_client, fake_simulate):
    payload = {
        "simulator_slot": "1",
        "host": "[::1]:9001",
        "cp_path": "CP-IPV6",
        "serial_number": "SERIAL-IPV6",
    }

    response = logged_in_client.post(reverse("ocpp:cp-simulator"), data=payload)

    assert response.status_code == 200
    params = get_simulator_state(cp=1, refresh_file=True)["params"]
    assert params["host"] == "::1"
    assert params["ws_port"] == 9001


def test_cp_simulator_form_prefers_default_simulator_host(logged_in_client):
    Simulator.objects.create(
        default=True,
        host="remote.example",
        ws_port=9443,
        cp_path="CP-DEFAULT",
        serial_number="SERIAL-DEFAULT",
    )

    response = logged_in_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="host" value="remote.example:9443"' in content


def test_cp_simulator_form_brackets_default_ipv6_host(logged_in_client):
    Simulator.objects.create(
        default=True,
        host="::1",
        ws_port=9001,
        cp_path="CP-IPV6-DEFAULT",
        serial_number="SERIAL-IPV6-DEFAULT",
    )

    response = logged_in_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="host" value="[::1]:9001"' in content


def test_cp_simulator_serial_defaults_to_cp_path_when_not_provided(
    logged_in_client, fake_simulate
):
    Simulator.objects.create(
        default=True,
        host="remote.example",
        ws_port=9000,
        cp_path="CP-DEFAULT",
        serial_number="SERIAL-DEFAULT",
    )

    payload = {
        "simulator_slot": "1",
        "host": "example.com:9000",
        "cp_path": "CP-FALLBACK",
        "serial_number": "",
    }
    response = logged_in_client.post(reverse("ocpp:cp-simulator"), data=payload)

    assert response.status_code == 200
    params = get_simulator_state(cp=1, refresh_file=True)["params"]
    assert params["cp_path"] == "CP-FALLBACK"
    assert params["serial_number"] == "CP-FALLBACK"


def test_cp_simulator_rejects_disabled_backend_selection(logged_in_client, monkeypatch):
    """Posted backend should be ignored when it is not in runtime-enabled choices."""

    monkeypatch.setattr(
        "apps.ocpp.views.simulator.get_simulator_backend_choices",
        lambda: (("arthexis", "arthexis"),),
    )

    response = logged_in_client.post(
        reverse("ocpp:cp-simulator"),
        data={"action": "select-backend", "simulator_backend": "mobilityhouse"},
    )

    assert response.status_code == 200
    assert response.context["selected_backend"] == "arthexis"
    assert response.wsgi_request.session.get("cp_simulator_backend") != "mobilityhouse"


def test_cp_simulator_shows_message_when_no_backends_available(logged_in_client, monkeypatch):
    """Simulator controls should gracefully handle a fully disabled backend configuration."""

    monkeypatch.setattr("apps.ocpp.views.simulator.get_simulator_backend_choices", lambda: ())

    response = logged_in_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    assert response.context["backends_available"] is False
    assert response.context["selected_backend"] == ""

    post_response = logged_in_client.post(
        reverse("ocpp:cp-simulator"),
        data={"action": "select-backend", "simulator_backend": "arthexis"},
    )

    assert post_response.status_code == 200
    assert "No simulator backends are enabled" in post_response.context["message"]
