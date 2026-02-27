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

