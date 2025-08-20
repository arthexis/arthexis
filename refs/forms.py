from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Reference


class ReferenceForm(forms.ModelForm):
    """Form to create a new reference."""

    file = forms.FileField(
        required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"})
    )

    class Meta:
        model = Reference
        fields = ["alt_text", "content_type", "value", "file"]
        labels = {
            "alt_text": _("Title / Alt Text"),
            "content_type": _("Content Type"),
            "value": _("Text or URL"),
            "file": _("Upload File"),
        }
        widgets = {
            "alt_text": forms.TextInput(attrs={"class": "form-control"}),
            "content_type": forms.Select(attrs={"class": "form-select"}),
            "value": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("value") and not cleaned.get("file"):
            raise forms.ValidationError("Provide text/URL or upload a file.")
        return cleaned

    def save(self, commit=True):
        instance: Reference = super().save(commit=False)
        uploaded = self.cleaned_data.get("file")
        if uploaded:
            if instance.content_type == Reference.TEXT:
                instance.value = uploaded.read().decode("utf-8", "ignore")
            else:
                instance.file.save(uploaded.name, uploaded, save=False)
        if commit:
            instance.save()
        return instance
