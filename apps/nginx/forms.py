from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.nginx.models import SiteConfiguration, parse_subdomain_prefixes


class SiteConfigurationForm(forms.ModelForm):
    class Meta:
        model = SiteConfiguration
        fields = "__all__"


class ManagedSubdomainForm(forms.Form):
    managed_subdomains = forms.CharField(
        required=False,
        label=_("Managed subdomains"),
        help_text=_(
            "Comma-separated subdomain prefixes to include for every managed site "
            "(for example: api, admin, status)."
        ),
        widget=forms.Textarea(attrs={"rows": 2, "cols": 40}),
    )

    def clean_managed_subdomains(self) -> str:
        raw = self.cleaned_data.get("managed_subdomains") or ""
        prefixes = parse_subdomain_prefixes(raw)
        return ", ".join(prefixes)
