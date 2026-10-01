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
    runtime_root = tmp_path / "runtime"
    controller = GwaySimulatorServiceController(
        manage_path=tmp_path / "manage.py",
        python="/opt/arthexis/.venv/bin/python",
        gway="/usr/local/bin/gway",
        sudo="/usr/bin/sudo",
        service_user="root",
        runtime_root=runtime_root,
        runner=runner,
    )

    controller.provision()

    command = calls[0][0]
    assert command[:3] == ["/usr/local/bin/gway", "service", "install"]
    assert "--no-enable" in command
    assert command[command.index("--name") + 1] == GWAY_SERVICE_NAME
    environment = command[command.index("--environment") + 1]
    assert environment == f"OCPP_SIMULATOR_RUNTIME_DIR={runtime_root.resolve()}"
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
        sudo="/usr/bin/sudo",
        service_user="arthe",
        runtime_root=tmp_path / "runtime",
    )

    assert controller.service_command == [
        "/usr/bin/sudo",
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
        sudo="/usr/bin/sudo",
        service_user="arthe",
        runtime_root=tmp_path / "runtime",
        runner=runner,
    )

    controller.stop()

    assert calls[0][:3] == ["/usr/bin/sudo", "-n", "/usr/local/bin/gway"]
    assert calls[0][3:5] == ["service", "stop"]


def test_ensure_started_refreshes_disabled_unit_before_start(monkeypatch, tmp_path):
    events = []
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    socket_path = runtime_root / "service.sock"
    socket_path.touch()

    controller = GwaySimulatorServiceController(
        manage_path=tmp_path / "manage.py",
        gway="/usr/local/bin/gway",
        sudo="/usr/bin/sudo",
        service_user="root",
        runtime_root=runtime_root,
    )
    monkeypatch.setattr(controller, "provision", lambda: events.append("provision"))
    monkeypatch.setattr(controller, "start", lambda: events.append("start"))

    controller.ensure_started()

    assert controller.service_socket == socket_path
    assert events == ["provision", "start"]
