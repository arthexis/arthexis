from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
import pytest

from apps.core.admin import OwnableAdminForm
from apps.groups.models import SecurityGroup
from apps.terminals import tasks
from apps.terminals.admin import AgentTerminalAdmin
from apps.terminals.models import AgentTerminal


User = get_user_model()


def test_assigned_to_any_user_resolves_direct_and_group_assignments(db):
    owner = User.objects.create_user(username="terminal-owner")
    group = SecurityGroup.objects.create(name="terminal-ops")
    group.user_set.add(owner)
    direct = AgentTerminal.objects.create(name="direct", user=owner)
    grouped = AgentTerminal.objects.create(name="grouped", group=group)

    matched_ids = set(AgentTerminal.assigned_to_any_user().values_list("id", flat=True))

    assert direct.id in matched_ids
    assert grouped.id in matched_ids


def test_admin_disables_add_permission(db):
    admin = AgentTerminalAdmin(AgentTerminal, AdminSite())
    request = RequestFactory().get("/admin/terminals/agentterminal/")
    request.user = User.objects.create_superuser(username="root", password="secret")

    assert admin.has_add_permission(request) is False


def test_launch_terminal_fails_fast_on_windows_before_posix_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHEXIS_TERMINAL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tasks.os, "name", "nt")
    terminal = AgentTerminal(name="windows-terminal", launch_command="echo ready")

    with pytest.raises(RuntimeError, match="_launch_terminal") as exc_info:
        tasks._launch_terminal(terminal)

    message = str(exc_info.value)
    assert "echo ready" not in message
    assert "startup_script=" not in message
    assert "executable=" not in message


def test_is_process_running_handles_windows_value_error(monkeypatch):
    def raise_value_error(pid, signal_number):
        raise ValueError("invalid pid")

    monkeypatch.setattr(tasks.os, "kill", raise_value_error)

    assert tasks._is_process_running(1234) is False


def test_admin_owner_fields_remain_editable_for_ownable_validation(db):
    admin = AgentTerminalAdmin(AgentTerminal, AdminSite())
    request = RequestFactory().get("/admin/terminals/agentterminal/")
    request.user = User.objects.create_superuser(username="owner-admin", password="secret")

    readonly_fields = set(admin.get_readonly_fields(request))
    assert {"user", "group", "avatar"}.isdisjoint(readonly_fields)

    form_class = admin.get_form(request)
    assert issubclass(form_class, OwnableAdminForm)
    assert {"user", "group", "avatar"}.issubset(form_class.base_fields)
