from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class PackageRepositoryForm(forms.Form):
    owner_repo = forms.CharField(
        label=_("Owner/Repository"),
        help_text=_("Enter the repository slug in the form owner/repository."),
        widget=forms.TextInput(attrs={"placeholder": "owner/repository"}),
    )
    description = forms.CharField(
        label=_("Description"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    private = forms.BooleanField(
        label=_("Private repository"),
        required=False,
        help_text=_("Mark the repository as private when checked."),
    )

    def clean_owner_repo(self):
        value = self.cleaned_data.get("owner_repo", "").strip()
        if "/" not in value:
            raise forms.ValidationError(_("Enter the owner/repository slug."))
        owner, repo = value.split("/", 1)
        owner = owner.strip()
        repo = repo.strip()
        if not owner or not repo:
            raise forms.ValidationError(_("Enter the owner/repository slug."))
        if " " in owner or " " in repo:
            raise forms.ValidationError(
                _("Owner and repository cannot contain spaces."),
            )
        self.cleaned_data["owner"] = owner
        self.cleaned_data["repo"] = repo
        return value
