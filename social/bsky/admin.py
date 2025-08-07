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
