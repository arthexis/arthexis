from django import forms
from django.contrib.sites.models import Site

from apps.app.models import Application

from ..models import SiteTemplate


class SiteForm(forms.ModelForm):
    name = forms.CharField(required=False)

    class Meta:
        model = Site
        fields = "__all__"


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
