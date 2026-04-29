from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.groups.models import SecurityGroup
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
