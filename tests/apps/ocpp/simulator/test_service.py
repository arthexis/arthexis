from unittest.mock import Mock

from apps.ocpp.simulator.service import (
    GWAY_SERVICE_NAME,
    GwaySimulatorServiceController,
)


def test_gway_service_is_provisioned_disabled(monkeypatch, tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return Mock(returncode=0)

    monkeypatch.setattr("apps.ocpp.simulator.service.os.geteuid", lambda: 0)
    controller = GwaySimulatorServiceController(
        manage_path=tmp_path / "manage.py",
        python="/opt/arthexis/.venv/bin/python",
        gway="/usr/local/bin/gway",
        service_user="root",
        runner=runner,
    )

    controller.provision()

    command = calls[0][0]
    assert command[:3] == ["/usr/local/bin/gway", "service", "install"]
    assert "--no-enable" in command
    assert command[command.index("--name") + 1] == GWAY_SERVICE_NAME
    assert command[-4:] == [
        "/opt/arthexis/.venv/bin/python",
        str(tmp_path / "manage.py"),
        "ocpp_simulator",
        "_service",
    ]


def test_system_service_executes_as_invoking_operator(tmp_path):
    controller = GwaySimulatorServiceController(
        manage_path=tmp_path / "manage.py",
        python="/opt/arthexis/.venv/bin/python",
        gway="/usr/local/bin/gway",
        service_user="arthe",
    )

    assert controller.service_command == [
        "sudo",
        "-n",
        "-u",
        "arthe",
        "--",
        "/opt/arthexis/.venv/bin/python",
        str(tmp_path / "manage.py"),
        "ocpp_simulator",
        "_service",
    ]


def test_non_root_service_operations_use_noninteractive_sudo(monkeypatch, tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return Mock(returncode=0)

    monkeypatch.setattr("apps.ocpp.simulator.service.os.geteuid", lambda: 1000)
    controller = GwaySimulatorServiceController(
        manage_path=tmp_path / "manage.py",
        gway="/usr/local/bin/gway",
        service_user="arthe",
        runner=runner,
    )

    controller.stop()

    assert calls[0][:3] == ["sudo", "-n", "/usr/local/bin/gway"]
    assert calls[0][3:5] == ["service", "stop"]


def test_ensure_started_refreshes_disabled_unit_before_start(monkeypatch, tmp_path):
    events = []
    socket_path = tmp_path / "service.sock"
    socket_path.touch()

    controller = GwaySimulatorServiceController(
        manage_path=tmp_path / "manage.py",
        gway="gway",
        service_user="root",
    )
    monkeypatch.setattr(
        "apps.ocpp.simulator.service.service_socket_path", lambda: socket_path
    )
    monkeypatch.setattr(controller, "provision", lambda: events.append("provision"))
    monkeypatch.setattr(controller, "start", lambda: events.append("start"))

    controller.ensure_started()

    assert events == ["provision", "start"]
