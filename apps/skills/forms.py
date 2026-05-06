from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class CodexSkillPackageImportForm(forms.Form):
    package = forms.FileField(
        label=_("Operator framework package"),
        help_text=_("Upload a .zip package exported from operator framework packages."),
        widget=forms.ClearableFileInput(
            attrs={"accept": ".zip,application/zip,application/x-zip-compressed"},
        ),
    )

    def clean_package(self):
        package = self.cleaned_data["package"]
        if not package.name.lower().endswith(".zip"):
            raise forms.ValidationError(_("Upload a .zip operator framework package."))
        return package
