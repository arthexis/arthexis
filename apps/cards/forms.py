from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.cards import mse
from apps.cards.models import CardFace, CardSet, OfferingSoul, get_cardface_bucket
from apps.cards.soul import MAX_UPLOAD_BYTES, SoulDerivationError
from apps.media.forms_mixins import MediaUploadAdminFormMixin
from apps.media.models import MediaFile


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


class CardFaceAdminForm(MediaUploadAdminFormMixin, forms.ModelForm):
    background_upload = forms.ImageField(
        required=False,
        label=_("Background upload"),
        help_text=_("Upload a printable background image for this card face."),
    )
    media_upload_bindings = {
        "background_upload": {
            "media_field": "background_media",
            "bucket_provider": get_cardface_bucket,
            "extra_validator": CardFace.validate_background_file,
        },
    }

    class Meta:
        model = CardFace
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_bucket_aware_querysets()

    def clean(self):
        cleaned = super().clean()
        background_media = cleaned.get("background_media")
        background_upload = cleaned.get("background_upload")
        if not background_media and not background_upload:
            raise ValidationError({"background_media": _("A background image is required.")})
        if background_upload and not background_media:
            bucket = self.get_media_bucket(get_cardface_bucket)
            cleaned["background_media"] = MediaFile(
                bucket=bucket,
                file=background_upload,
                original_name=background_upload.name,
                content_type=getattr(background_upload, "content_type", "") or "",
                size=getattr(background_upload, "size", 0) or 0,
            )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        self.store_uploads_on_instance(instance)
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_background_upload(self):
        return self.clean_upload_field("background_upload")


class CardSetUploadForm(forms.Form):
    mse_set = forms.FileField(
        label=_("MSE set file"),
        help_text=_("Upload a .mse-set file or a plain text set file."),
    )

    def clean_mse_set(self):
        uploaded_file = self.cleaned_data.get("mse_set")
        if not uploaded_file:
            return uploaded_file

        try:
            payload = uploaded_file.read()
            uploaded_file.seek(0)

            set_text = mse.extract_set_text(payload)
            parsed = mse.parse_mse_set(set_text)

            self.cleaned_data["set_text"] = set_text
            self.cleaned_data["parsed_data"] = parsed
            return uploaded_file
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                _("Failed to parse MSE set file: %(error)s") % {"error": exc}
            ) from exc

    def save(self) -> CardSet:
        uploaded = self.cleaned_data["mse_set"]
        set_text = self.cleaned_data["set_text"]
        parsed_data = self.cleaned_data["parsed_data"]
        filename = getattr(uploaded, "name", "") or ""
        return CardSet.create_from_parsed(parsed_data, set_text, filename=filename)


class OfferingSoulUploadForm(forms.Form):
    offering_file = forms.FileField(
        label=_("Offering file"),
        help_text=_("Upload any file up to 25 MB. 10 MB or less is recommended."),
    )
    issuance_marker = forms.CharField(
        required=False,
        max_length=64,
        label=_("Issuance marker"),
        help_text=_("Optional deterministic marker to version card issuance behavior."),
    )

    def clean_offering_file(self):
        uploaded_file = self.cleaned_data.get("offering_file")
        if not uploaded_file:
            return uploaded_file
        size_bytes = int(getattr(uploaded_file, "size", 0) or 0)
        if size_bytes <= 0:
            raise ValidationError(_("Uploaded file is empty."))
        if size_bytes > MAX_UPLOAD_BYTES:
            raise ValidationError(_("Uploaded file exceeds the 25 MB limit."))
        return uploaded_file

    def save(self) -> OfferingSoul:
        uploaded_file = self.cleaned_data["offering_file"]
        issuance_marker = self.cleaned_data.get("issuance_marker", "")
        try:
            return OfferingSoul.create_from_upload(
                uploaded_file=uploaded_file,
                issuance_marker=issuance_marker,
            )
        except SoulDerivationError as exc:
            raise ValidationError(str(exc)) from exc
