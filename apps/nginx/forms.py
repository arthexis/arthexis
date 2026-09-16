from __future__ import annotations

from django import forms

from apps.nginx.models import SiteConfiguration


class SiteConfigurationForm(forms.ModelForm):
    """Passive form retained while legacy SiteConfiguration rows exist."""

    class Meta:
        model = SiteConfiguration
        fields = "__all__"
