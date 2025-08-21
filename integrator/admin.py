import xmlrpc.client
from urllib.parse import urljoin

from django import forms
from django.contrib import admin, messages

from config.offline import requires_network
from .models import BskyAccount, OdooInstance, RequestType, Request
from django.apps import apps
from django.shortcuts import render, redirect
from django.urls import path

from .models import Entity


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
    actions = ["test_credentials"]

    @requires_network
    def test_credentials(self, request, queryset):
        for account in queryset:
            from atproto import Client

            try:
                client = Client()
                client.login(account.handle, account.app_password)
            except Exception as exc:  # pragma: no cover - relies on SDK errors
                self.message_user(
                    request,
                    f"{account.handle}: {exc}",
                    level=messages.ERROR,
                )
            else:
                self.message_user(request, f"{account.handle}: success")

    test_credentials.short_description = "Test credentials"

@admin.register(OdooInstance)
class OdooInstanceAdmin(admin.ModelAdmin):
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

    test_connection.short_description = "Test API connection"


@admin.register(RequestType)
class RequestTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "next_number")


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "request_type",
        "requester",
        "approver",
        "status",
        "responded_at",
    )
    readonly_fields = ("number", "responded_at", "requester")


def seed_data_view(request):
    seed_items = []
    for model in apps.get_models():
        if issubclass(model, Entity):
            for obj in model.all_objects.filter(is_seed_data=True):
                seed_items.append(
                    {
                        "model_verbose_name": model._meta.verbose_name,
                        "model_app_label": model._meta.app_label,
                        "model_name": model._meta.model_name,
                        "obj": obj,
                    }
                )
    if request.method == "POST":
        app_label = request.POST["app"]
        model_name = request.POST["model"]
        pk = request.POST["pk"]
        model = apps.get_model(app_label, model_name)
        obj = model.all_objects.get(pk=pk, is_seed_data=True)
        obj.is_deleted = False
        obj.save(update_fields=["is_deleted"])
        return redirect("admin:seed-data")
    context = dict(admin.site.each_context(request), seed_items=seed_items)
    return render(request, "admin/seed_data.html", context)


original_get_urls = admin.site.get_urls


def get_urls():
    urls = original_get_urls()
    custom = [
        path("seed-data/", admin.site.admin_view(seed_data_view), name="seed-data"),
    ]
    return custom + urls


admin.site.get_urls = get_urls
