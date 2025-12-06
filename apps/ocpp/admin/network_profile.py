from .common_imports import *

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
