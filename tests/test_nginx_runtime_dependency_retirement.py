from apps.core import system_ui
from apps.core.system import ui
from apps.core.system.admin_views import TASK_PANEL_ROUTES
from apps.core.system.ui import network_probe, services


def test_system_ui_no_longer_exposes_nginx_diagnostics():
    assert not hasattr(ui, "_build_nginx_report")
    assert not hasattr(ui, "build_nginx_report")
    assert not hasattr(system_ui, "build_nginx_report")
    assert not hasattr(network_probe, "_build_nginx_report")
    assert not hasattr(services, "NginxReportPayload")


def test_system_admin_no_longer_registers_nginx_report():
    assert "system-nginx-report" not in {route.name for route in TASK_PANEL_ROUTES}


def test_system_ui_has_no_runtime_apps_nginx_imports():
    for module in (ui, system_ui, network_probe, services):
        source_path = module.__file__
        assert source_path is not None
        with open(source_path, encoding="utf-8") as handle:
            assert "apps.nginx" not in handle.read()
