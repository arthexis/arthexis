from django import forms
from django.utils.translation import gettext_lazy as _

from apps.ocpp.models import ChargerVendorSubmission


class ChargerVendorSubmissionForm(forms.ModelForm):
    """Collect charger vendor details needed for integration evaluation."""

    class Meta:
        """Bind the public intake form to charger vendor submissions and widgets."""

        model = ChargerVendorSubmission
        fields = [
            "company_name",
            "contact_name",
            "contact_email",
            "contact_phone",
            "website",
            "charger_brand",
            "charger_models",
            "ocpp_versions",
            "connectivity_summary",
            "api_documentation_url",
            "certification_summary",
            "deployment_regions",
            "deployment_volume",
            "remote_access_method",
            "hardware_notes",
            "integration_goals",
            "additional_notes",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"class": "form-control"}),
            "contact_name": forms.TextInput(attrs={"class": "form-control"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            "contact_phone": forms.TextInput(attrs={"class": "form-control"}),
            "website": forms.URLInput(attrs={"class": "form-control"}),
            "charger_brand": forms.TextInput(attrs={"class": "form-control"}),
            "charger_models": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "ocpp_versions": forms.TextInput(attrs={"class": "form-control"}),
            "connectivity_summary": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "api_documentation_url": forms.URLInput(attrs={"class": "form-control"}),
            "certification_summary": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "deployment_regions": forms.TextInput(attrs={"class": "form-control"}),
            "deployment_volume": forms.TextInput(attrs={"class": "form-control"}),
            "remote_access_method": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "hardware_notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "integration_goals": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "additional_notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
        }

    def clean_ocpp_versions(self) -> str:
        """Normalize OCPP version input for consistent admin review."""

        return " ".join((self.cleaned_data.get("ocpp_versions") or "").split())

