from __future__ import annotations

import os
from typing import Iterable

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import AWSCredentials


def _region_choices() -> list[tuple[str, str]]:
    try:
        import boto3
    except ModuleNotFoundError:
        return [("us-east-1", "us-east-1")]

    session = boto3.session.Session()
    regions: Iterable[str] = session.get_available_regions("lightsail") or []
    normalized = sorted({code for code in regions})
    if not normalized:
        normalized = ["us-east-1"]
    return [(code, code) for code in normalized]


class BaseLightsailFetchForm(forms.Form):
    name = forms.CharField(label=_("Name"))
    region = forms.ChoiceField(label=_("Region"))
    credentials = forms.ModelChoiceField(
        queryset=AWSCredentials.objects.all(),
        label=_("Saved credentials"),
        required=False,
        help_text=_("Optional: use an existing AWS credential pair."),
    )
    credential_label = forms.CharField(
        label=_("Credential name"),
        required=False,
        help_text=_("Label for storing a new credential pair, if provided."),
    )
    access_key_id = forms.CharField(
        label=_("AWS access key ID"),
        required=False,
        help_text=_("Only required when no saved credentials or environment variables are available."),
    )
    secret_access_key = forms.CharField(
        label=_("AWS secret access key"),
        required=False,
        widget=forms.PasswordInput(render_value=True),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["region"].choices = _region_choices()
        default_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        if default_region:
            self.fields["region"].initial = default_region
        self.fields["credentials"].queryset = AWSCredentials.objects.order_by("name")

    def clean(self):
        data = super().clean()
        access_key = data.get("access_key_id")
        secret_key = data.get("secret_access_key")
        stored = data.get("credentials")
        provided_pair = access_key and secret_key
        env_access = os.getenv("AWS_ACCESS_KEY_ID")
        env_secret = os.getenv("AWS_SECRET_ACCESS_KEY")

        if secret_key and not access_key:
            self.add_error("access_key_id", _("Provide the access key for the supplied secret."))
        if access_key and not secret_key:
            self.add_error("secret_access_key", _("Provide the secret key for the supplied access key."))

        if stored is None and not provided_pair and not (env_access and env_secret):
            raise forms.ValidationError(
                _("Enter AWS credentials or configure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."),
                code="missing-credentials",
            )
        return data


class FetchInstanceForm(BaseLightsailFetchForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = _("Instance name")


class FetchDatabaseForm(BaseLightsailFetchForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = _("Database name")
