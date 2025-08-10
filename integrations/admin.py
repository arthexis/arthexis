from django import forms
from django.contrib import admin

from .models import BskyAccount


class BskyAccountAdminForm(forms.ModelForm):
    class Meta:
        model = BskyAccount
        fields = ("user", "handle", "app_password")
        help_texts = {
            "app_password": (
                "Create an app password at "
                "https://bsky.app/settings/app-passwords and enter it here. "
                "It will be used to authenticate with the Bluesky API."
            ),
        }

    def clean(self):
        cleaned = super().clean()
        handle = cleaned.get("handle")
        password = cleaned.get("app_password")
        if handle and password:
            from atproto import Client

            try:
                client = Client()
                client.login(handle, password)
            except Exception as exc:  # pragma: no cover - relies on SDK errors
                raise forms.ValidationError(
                    f"Could not verify credentials with Bluesky: {exc}"
                )
        return cleaned


@admin.register(BskyAccount)
class BskyAccountAdmin(admin.ModelAdmin):
    form = BskyAccountAdminForm
    list_display = ("user", "handle")

from django.contrib import messages
import xmlrpc.client
from urllib.parse import urljoin

from .models import Instance
from config.offline import requires_network


@admin.register(Instance)
class InstanceAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "database", "username")
    actions = ["test_connection"]

    @requires_network
    def test_connection(self, request, queryset):
        for instance in queryset:
            server = xmlrpc.client.ServerProxy(
                urljoin(instance.url, "/xmlrpc/2/common")
            )
            try:
                uid = server.authenticate(
                    instance.database,
                    instance.username,
                    instance.password,
                    {},
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f"{instance.name}: {exc}",
                    level=messages.ERROR,
                )
                continue

            if uid:
                self.message_user(request, f"{instance.name}: success")
            else:
                self.message_user(
                    request,
                    f"{instance.name}: invalid credentials",
                    level=messages.ERROR,
                )

    test_connection.short_description = "Test connection"
