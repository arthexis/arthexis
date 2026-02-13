"""Forms for Google Sheets discovery utilities."""

from django import forms

from .models import DriveAccount


class SheetDiscoveryForm(forms.Form):
    """Discover or update a tracked sheet from a URL and optional account."""

    sheet_url = forms.URLField(label="Sheet URL", assume_scheme="https")
    drive_account = forms.ModelChoiceField(
        queryset=DriveAccount.objects.all(),
        required=False,
        empty_label="Public sheet (no account)",
    )
