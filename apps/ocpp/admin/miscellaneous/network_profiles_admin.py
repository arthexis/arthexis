from ..common_imports import *


class SetNetworkProfileForm(forms.Form):
    chargers = forms.ModelMultipleChoiceField(
        label=_("Charge points"),
        queryset=Charger.objects.none(),
        help_text=_("Select EVCS units that should receive this network profile."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["chargers"].queryset = (
            Charger.objects.filter(connector_id__isnull=True)
            .order_by("display_name", "charger_id")
            .all()
        )

    def clean(self):
        cleaned = super().clean()
        chargers = cleaned.get("chargers")
        if not chargers:
            self.add_error("chargers", _("Select at least one charge point."))
        return cleaned


@admin.register(CPNetworkProfile)
class CPNetworkProfileAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "configuration_slot",
        "created_at",
        "updated_at",
    )
    list_filter = ("configuration_slot",)
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CPNetworkProfileDeployment)
class CPNetworkProfileDeploymentAdmin(EntityModelAdmin):
    list_display = (
        "network_profile",
        "charger",
        "status",
        "status_timestamp",
        "requested_at",
        "completed_at",
    )
    list_filter = ("status",)
    search_fields = (
        "network_profile__name",
        "charger__charger_id",
        "ocpp_message_id",
    )
    readonly_fields = ("requested_at", "created_at", "updated_at")
