from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.cards.models import CardFace, get_cardface_bucket
from apps.media.models import MediaFile
from apps.media.utils import create_media_file


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


class CardFaceAdminForm(forms.ModelForm):
    background_upload = forms.ImageField(
        required=False,
        label=_("Background upload"),
        help_text=_("Upload a printable background image for this card face."),
    )

    class Meta:
        model = CardFace
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bucket = get_cardface_bucket()
        self.fields["background_media"].queryset = MediaFile.objects.filter(bucket=bucket)

    def clean(self):
        cleaned = super().clean()
        background_media = cleaned.get("background_media")
        background_upload = cleaned.get("background_upload")
        if not background_media and not background_upload:
            raise ValidationError({"background_media": _("A background image is required.")})
        if background_upload:
            bucket = get_cardface_bucket()
            if not bucket.allows_filename(background_upload.name):
                raise ValidationError({"background_upload": _("File type is not allowed.")})
            if not bucket.allows_size(background_upload.size):
                raise ValidationError({"background_upload": _("File exceeds the allowed size.")})
            CardFace.validate_background_file(background_upload)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        upload = self.cleaned_data.get("background_upload")
        if upload:
            bucket = get_cardface_bucket()
            media_file = create_media_file(bucket=bucket, uploaded_file=upload)
            instance.background_media = media_file
        if commit:
            instance.save()
            self.save_m2m()
        return instance
