from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.nginx import services
from apps.nginx.models import SiteConfiguration


class SiteConfigurationForm(forms.ModelForm):
    secondary_instance = forms.ChoiceField(
        required=False,
        label=_("Secondary instance"),
    )

    class Meta:
        model = SiteConfiguration
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secondary_instance"].choices = self._secondary_instance_choices()

    def _secondary_instance_choices(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = [("", _("None (single instance)"))]
        for instance in services.discover_secondary_instances():
            description = _("%(name)s (port %(port)s, role %(role)s)") % {
                "name": instance.name,
                "port": instance.port,
                "role": instance.role,
            }
            choices.append((instance.name, description))
        return choices
