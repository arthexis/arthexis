from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import NodeManager

from . import godaddy as dns_utils
from .models import GoDaddyDNSRecord


class DeployDNSRecordsForm(forms.Form):
    manager = forms.ModelChoiceField(
        label="Node Profile",
        queryset=NodeManager.objects.none(),
        help_text="Credentials used to authenticate with the DNS provider.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = NodeManager.objects.filter(
            provider=NodeManager.Provider.GODADDY, is_enabled=True
        )


@admin.register(GoDaddyDNSRecord)
class GoDaddyDNSRecordAdmin(EntityModelAdmin):
    list_display = ("record_type", "fqdn", "node_manager", "ttl", "last_synced_at")
    list_filter = ("record_type", "node_manager")
    search_fields = ("domain", "name", "data")
    autocomplete_fields = ("node_manager",)
    actions = ["deploy_selected_records", "validate_selected_records"]

    def _default_manager_for_queryset(self, queryset) -> NodeManager | None:
        manager_ids = list(
            queryset.exclude(node_manager__isnull=True)
            .values_list("node_manager_id", flat=True)
            .distinct()
        )
        if len(manager_ids) == 1:
            return manager_ids[0]
        available = list(
            NodeManager.objects.filter(
                provider=NodeManager.Provider.GODADDY, is_enabled=True
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
                manager = form.cleaned_data["manager"]
                result = manager.publish_dns_records(list(queryset))
                for record, reason in result.skipped.items():
                    self.message_user(request, f"{record}: {reason}", messages.WARNING)
                for record, reason in result.failures.items():
                    self.message_user(request, f"{record}: {reason}", messages.ERROR)
                if result.deployed:
                    self.message_user(
                        request,
                        f"Deployed {len(result.deployed)} DNS record(s) via {manager}.",
                        messages.SUCCESS,
                    )
                return None
        else:
            initial_manager = self._default_manager_for_queryset(queryset)
            form = DeployDNSRecordsForm(initial={"manager": initial_manager})

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
