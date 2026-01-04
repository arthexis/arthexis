from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.cards.models import CardFace


class CardFacePreviewForm(forms.Form):
    overlay_one_text = forms.CharField(label=_("Overlay 1 text"), required=False)
    overlay_two_text = forms.CharField(label=_("Overlay 2 text"), required=False)

    overlay_one_font = forms.ChoiceField(label=_("Overlay 1 font"), required=False)
    overlay_two_font = forms.ChoiceField(label=_("Overlay 2 font"), required=False)

    overlay_one_font_size = forms.IntegerField(label=_("Overlay 1 size"), min_value=1, required=False)
    overlay_two_font_size = forms.IntegerField(label=_("Overlay 2 size"), min_value=1, required=False)

    overlay_one_x = forms.IntegerField(label=_("Overlay 1 X"), required=False)
    overlay_one_y = forms.IntegerField(label=_("Overlay 1 Y"), required=False)
    overlay_two_x = forms.IntegerField(label=_("Overlay 2 X"), required=False)
    overlay_two_y = forms.IntegerField(label=_("Overlay 2 Y"), required=False)

    def __init__(self, *args, fonts=None, sigils=None, **kwargs):
        self.sigil_tokens = sigils or []
        super().__init__(*args, **kwargs)

        font_choices = fonts or CardFace.font_choices()
        self.fields["overlay_one_font"].choices = font_choices
        self.fields["overlay_two_font"].choices = font_choices

        for token in self.sigil_tokens:
            field_name = CardFace.sigil_field_name(token)
            self.fields[field_name] = forms.CharField(
                label=f"[{token}]", required=False, help_text=_("Manual sigil value for preview")
            )

    def sigil_overrides(self) -> dict[str, str]:
        overrides: dict[str, str] = {}
        if not self.is_bound or not self.is_valid():
            return overrides
        for token in self.sigil_tokens:
            field_name = CardFace.sigil_field_name(token)
            value = self.cleaned_data.get(field_name)
            if value is not None:
                overrides[token.lower()] = value
        return overrides
