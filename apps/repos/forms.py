from __future__ import annotations

import secrets

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.repos.models.github_apps import GitHubApp


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


class GitHubAppAdminForm(forms.ModelForm):
    webhook_url_preview = forms.CharField(
        label=_("Webhook URL (computed)"),
        required=False,
        disabled=True,
        help_text=_(
            "Read-only URL derived from the webhook slug and current site settings."
        ),
    )

    class Meta:
        model = GitHubApp
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reorder_webhook_preview()

        base_url = GitHubApp.instance_base_url()
        default_slug = "gh-app"

        if self.instance.pk:
            computed = self.instance.webhook_full_url()
            self.fields["webhook_url_preview"].initial = computed
            return

        slug_source = self.data if self.is_bound else self.initial
        slug = slug_source.get("webhook_slug") or default_slug
        full_webhook_url = f"{base_url}{GitHubApp.webhook_path(slug)}"
        self.fields["webhook_url_preview"].initial = full_webhook_url

        if self.is_bound:
            return

        self.initial.setdefault("webhook_slug", slug)
        self.initial.setdefault("webhook_url", full_webhook_url)
        self.initial.setdefault("webhook_secret", secrets.token_urlsafe(32))
        self.initial.setdefault("homepage_url", base_url)
        self.initial.setdefault("callback_url", base_url)
        self.initial.setdefault("setup_url", base_url)
        self.initial.setdefault("redirect_url", base_url)

    def _reorder_webhook_preview(self) -> None:
        if "webhook_url_preview" not in self.fields or "webhook_url" not in self.fields:
            return
        fields = list(self.fields)
        fields.remove("webhook_url_preview")
        insert_at = fields.index("webhook_url") + 1
        fields.insert(insert_at, "webhook_url_preview")
        self.order_fields(fields)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("webhook_slug") and not cleaned.get("webhook_url"):
            cleaned["webhook_url"] = (
                f"{GitHubApp.instance_base_url()}"
                f"{GitHubApp.webhook_path(cleaned.get('webhook_slug'))}"
            )
        return cleaned
