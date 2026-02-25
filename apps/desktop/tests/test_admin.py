"""Tests for desktop admin actions."""

from __future__ import annotations

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from apps.desktop.admin import RegisteredExtensionAdmin
from apps.desktop.models import RegisteredExtension


class _MessageCollectorAdmin(RegisteredExtensionAdmin):
    """Admin subclass that collects sent messages for assertions."""

    def __init__(self, model, admin_site):
        super().__init__(model, admin_site)
        self.collected_messages: list[str] = []

    def message_user(self, request, message, level=None, extra_tags="", fail_silently=False):
        """Capture outgoing admin messages."""

        self.collected_messages.append(str(message))


def test_register_selected_extensions_action(monkeypatch, db) -> None:
    """Admin action should attempt registration for each selected extension."""

    extension = RegisteredExtension.objects.create(
        extension=".csv",
        django_command="import_csv",
    )

    admin_instance = _MessageCollectorAdmin(RegisteredExtension, AdminSite())

    def _fake_register(selected_extension):
        assert selected_extension.pk == extension.pk
        return type("R", (), {"success": True, "message": "ok"})()

    monkeypatch.setattr("apps.desktop.admin.register_extension_with_os", _fake_register)

    request = HttpRequest()
    request.user = get_user_model().objects.create_superuser(
        username="action-admin", email="action@example.com", password="pwd12345"
    )

    admin_instance.register_selected_extensions(request, RegisteredExtension.objects.filter(pk=extension.pk))

    assert admin_instance.collected_messages == [".csv: ok"]
