from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Reference


class ReferenceForm(forms.ModelForm):
    """Form to create a new reference."""

    class Meta:
        model = Reference
        fields = ["value", "alt_text"]
        labels = {
            "value": _("Value / URL"),
            "alt_text": _("Alt text"),
        }
        widgets = {
            "value": forms.TextInput(attrs={"class": "form-control"}),
            "alt_text": forms.TextInput(attrs={"class": "form-control"}),
        }
