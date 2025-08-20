from django import forms

from .models import Reference


class ReferenceForm(forms.ModelForm):
    """Form to create a new reference."""

    class Meta:
        model = Reference
        fields = ["value", "alt_text"]
