from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from . import godaddy as dns_utils
from .models import DNSProviderCredential, GoDaddyDNSRecord


class DeployDNSRecordsForm(forms.Form):
    credentials = forms.ModelChoiceField(
        label="DNS credentials",
        queryset=DNSProviderCredential.objects.none(),
        help_text="Credentials used to authenticate with the DNS provider.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["credentials"].queryset = DNSProviderCredential.objects.filter(
            provider=DNSProviderCredential.Provider.GODADDY, is_enabled=True
        )


@admin.register(GoDaddyDNSRecord)
class GoDaddyDNSRecordAdmin(EntityModelAdmin):
    list_display = ("record_type", "fqdn", "credentials", "ttl", "last_synced_at")
    list_filter = ("record_type", "credentials")
    search_fields = ("domain", "name", "data")
    autocomplete_fields = ("credentials",)
    actions = ["deploy_selected_records", "validate_selected_records"]

    def _default_credentials_for_queryset(
        self, queryset,
    ) -> DNSProviderCredential | None:
        credential_ids = list(
            queryset.exclude(credentials__isnull=True)
            .values_list("credentials_id", flat=True)
            .distinct()
        )
        if len(credential_ids) == 1:
            return credential_ids[0]
        available = list(
            DNSProviderCredential.objects.filter(
                provider=DNSProviderCredential.Provider.GODADDY, is_enabled=True
            ).values_list("pk", flat=True)
        )
        if len(available) == 1:
            return available[0]
        return None

    @admin.action(description="Deploy Selected records")
    def deploy_selected_records(self, request, queryset):
        if "apply" in request.POST:
            form = DeployDNSRecordsForm(request.POST)
            if form.is_valid():
                credentials = form.cleaned_data["credentials"]
                result = credentials.publish_dns_records(list(queryset))
                for record, reason in result.skipped.items():
                    self.message_user(request, f"{record}: {reason}", messages.WARNING)
                for record, reason in result.failures.items():
                    self.message_user(request, f"{record}: {reason}", messages.ERROR)
                if result.deployed:
                    self.message_user(
                        request,
                        f"Deployed {len(result.deployed)} DNS record(s) via {credentials}.",
                        messages.SUCCESS,
                    )
                return None
        else:
            initial_credentials = self._default_credentials_for_queryset(queryset)
            form = DeployDNSRecordsForm(initial={"credentials": initial_credentials})

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "queryset": queryset,
            "title": "Deploy DNS records",
        }
        return render(
            request,
            "admin/dns/godaddydnsrecord/deploy_records.html",
            context,
        )

    @admin.action(description="Validate Selected records")
    def validate_selected_records(self, request, queryset):
        resolver = dns_utils.create_resolver()
        successes = 0
        for record in queryset:
            ok, message = dns_utils.validate_record(record, resolver=resolver)
            if ok:
                successes += 1
            else:
                self.message_user(request, f"{record}: {message}", messages.ERROR)

        if successes:
            self.message_user(
                request, f"{successes} record(s) validated successfully.", messages.SUCCESS
            )


@admin.register(DNSProviderCredential)
class DNSProviderCredentialAdmin(OwnableAdminMixin, EntityModelAdmin):
    list_display = ("__str__", "provider", "is_enabled", "default_domain")
    list_filter = ("provider", "is_enabled")
    search_fields = (
        "default_domain",
        "user__username",
        "group__name",
    )
    fieldsets = (
        (_("Owner"), {"fields": ("user", "group", "avatar")}),
        (
            _("Credentials"),
            {"fields": ("api_key", "api_secret", "customer_id")},
        ),
        (
            _("Configuration"),
            {
                "fields": (
                    "provider",
                    "default_domain",
                    "use_sandbox",
                    "is_enabled",
                )
            },
        ),
    )
