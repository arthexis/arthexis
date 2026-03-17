from django import forms
from django.conf import settings
from django.contrib.sites.models import Site
from django.utils.translation import gettext_lazy as _

from apps.app.models import Application

from ..models import SiteTemplate


class SiteForm(forms.ModelForm):
    """Model form for configuring per-site behavior."""

    name = forms.CharField(required=False)
    allowed_languages = forms.MultipleChoiceField(
        required=False,
        choices=(),
        label=_("Allowed languages"),
        help_text=_(
            "Restrict selectable languages for this site. Leave empty to allow all configured languages."
        ),
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Site
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        """Initialize language fields using configured project languages."""

        super().__init__(*args, **kwargs)
        language_choices = tuple(getattr(settings, "LANGUAGES", ()))
        self.fields["default_language"].choices = language_choices
        self.fields["allowed_languages"].choices = language_choices
        allowed_languages = getattr(self.instance, "allowed_languages", None) or []
        self.initial.setdefault("allowed_languages", allowed_languages)

    def clean_allowed_languages(self):
        """Normalize and deduplicate selected allowed language codes."""

        normalized_codes: list[str] = []
        for code in self.cleaned_data.get("allowed_languages") or []:
            normalized = (code or "").strip().replace("_", "-").lower()[:15]
            if normalized and normalized not in normalized_codes:
                normalized_codes.append(normalized)
        return normalized_codes

    def clean(self):
        """Validate that the site default language is included when restricted."""

        cleaned_data = super().clean()
        default_language = (cleaned_data.get("default_language") or "").strip()
        allowed_languages = cleaned_data.get("allowed_languages") or []
        if allowed_languages and default_language and default_language not in allowed_languages:
            self.add_error(
                "default_language",
                _("Default language must be part of the allowed languages list."),
            )
        return cleaned_data


class SiteTemplateAdminForm(forms.ModelForm):
    color_fields = (
        "primary_color",
        "primary_color_emphasis",
        "accent_color",
        "accent_color_emphasis",
        "support_color",
        "support_color_emphasis",
        "support_text_color",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.color_fields:
            value = self.initial.get(field_name) or getattr(self.instance, field_name, None)
            if isinstance(value, str) and len(value) == 4:
                # Preserve 3-digit shorthand colors by using a text widget that accepts #rgb.
                self.fields[field_name].widget = forms.TextInput(attrs={"type": "text"})

    class Meta:
        model = SiteTemplate
        fields = "__all__"
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "primary_color_emphasis": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color_emphasis": forms.TextInput(attrs={"type": "color"}),
            "support_color": forms.TextInput(attrs={"type": "color"}),
            "support_color_emphasis": forms.TextInput(attrs={"type": "color"}),
            "support_text_color": forms.TextInput(attrs={"type": "color"}),
        }


class ApplicationForm(forms.ModelForm):
    name = forms.CharField(required=False)

    class Meta:
        model = Application
        fields = "__all__"
