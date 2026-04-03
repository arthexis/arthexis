from django import forms
from django.contrib.sites.models import Site

from apps.app.models import Application

from ..models import SiteProfile, SiteTemplate


class SiteForm(forms.ModelForm):
    name = forms.CharField(required=False)
    template = forms.ModelChoiceField(
        queryset=SiteTemplate.objects.all(),
        required=False,
    )
    default_landing = forms.ModelChoiceField(
        queryset=None,
        required=False,
    )
    interface_landing = forms.ModelChoiceField(
        queryset=None,
        required=False,
    )
    managed = forms.BooleanField(required=False)
    require_https = forms.BooleanField(required=False)
    enable_public_chat = forms.BooleanField(required=False)

    class Meta:
        model = Site
        fields = (
            "domain",
            "name",
            "template",
            "default_landing",
            "interface_landing",
            "managed",
            "require_https",
            "enable_public_chat",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        profile = getattr(self.instance, "profile", None)

        default_field = SiteProfile._meta.get_field("default_landing")
        interface_field = SiteProfile._meta.get_field("interface_landing")
        self.fields[
            "default_landing"
        ].queryset = default_field.remote_field.model.objects.filter(
            **default_field.get_limit_choices_to()
        )
        self.fields[
            "interface_landing"
        ].queryset = interface_field.remote_field.model.objects.filter(
            **interface_field.get_limit_choices_to()
        )

        if profile is not None:
            self.initial.setdefault("template", profile.template_id)
            self.initial.setdefault("default_landing", profile.default_landing_id)
            self.initial.setdefault("interface_landing", profile.interface_landing_id)
            self.initial.setdefault("managed", profile.managed)
            self.initial.setdefault("require_https", profile.require_https)
            self.initial.setdefault("enable_public_chat", profile.enable_public_chat)

    def save(self, commit=True):
        site = super().save(commit=commit)
        profile_values = {
            "template": self.cleaned_data.get("template"),
            "default_landing": self.cleaned_data.get("default_landing"),
            "interface_landing": self.cleaned_data.get("interface_landing"),
            "managed": bool(self.cleaned_data.get("managed")),
            "require_https": bool(self.cleaned_data.get("require_https")),
            "enable_public_chat": bool(self.cleaned_data.get("enable_public_chat")),
        }

        def save_profile() -> None:
            if not site.pk:
                return
            profile, _created = SiteProfile.objects.get_or_create(site=site)
            updated_fields: list[str] = []
            for field, value in profile_values.items():
                if getattr(profile, field) != value:
                    setattr(profile, field, value)
                    updated_fields.append(field)
            if updated_fields:
                profile.save(update_fields=updated_fields)

        if commit:
            save_profile()
        else:
            original_save_m2m = self.save_m2m

            def save_m2m():
                original_save_m2m()
                save_profile()

            self.save_m2m = save_m2m
        return site


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
            value = self.initial.get(field_name) or getattr(
                self.instance, field_name, None
            )
            if isinstance(value, str) and len(value) == 4:
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
