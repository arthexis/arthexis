"""Forms for sponsor registration."""

from __future__ import annotations

from dataclasses import dataclass

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import SponsorTier, Sponsorship, configured_payment_processors


@dataclass(frozen=True)
class PaymentProcessorChoice:
    content_type_id: int
    object_id: int
    label: str

    @property
    def value(self) -> str:
        return f"{self.content_type_id}:{self.object_id}"


def payment_processor_choices() -> list[PaymentProcessorChoice]:
    choices: list[PaymentProcessorChoice] = []
    for processor in configured_payment_processors():
        content_type = ContentType.objects.get_for_model(processor.__class__)
        label = f"{processor._meta.verbose_name} ({processor.identifier()})"
        choices.append(
            PaymentProcessorChoice(
                content_type_id=content_type.pk,
                object_id=processor.pk,
                label=label,
            )
        )
    return choices


def resolve_processor(selection: str):
    if not selection:
        return None
    try:
        content_type_id, object_id = selection.split(":", 1)
        content_type = ContentType.objects.get(pk=int(content_type_id))
    except (ValueError, TypeError, ContentType.DoesNotExist) as exc:
        raise ValidationError(_("Selected payment processor is not available.")) from exc

    model_class = content_type.model_class()
    if model_class is None:
        raise ValidationError(_("Selected payment processor is not available."))
    try:
        return model_class.objects.get(pk=int(object_id))
    except model_class.DoesNotExist as exc:  # type: ignore[attr-defined]
        raise ValidationError(_("Selected payment processor is not available.")) from exc


class SponsorRegistrationForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput())
    tier = forms.ModelChoiceField(
        queryset=SponsorTier.objects.filter(is_active=True), empty_label=None
    )
    renewal_mode = forms.ChoiceField(choices=Sponsorship.RenewalMode.choices)
    payment_processor = forms.ChoiceField(choices=())
    payment_reference = forms.CharField(
        max_length=255,
        required=False,
        help_text=_("Optional payment confirmation reference."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")
        choices = payment_processor_choices()
        if choices:
            self.fields["payment_processor"].choices = [
                (choice.value, choice.label) for choice in choices
            ]
        else:
            self.fields["payment_processor"].required = False
            self.fields["payment_processor"].choices = []
            self.fields["payment_processor"].widget.attrs["disabled"] = "disabled"

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        if not self.fields["payment_processor"].choices:
            raise ValidationError(_("Configure a payment processor before registering."))
        user_model = get_user_model()
        username = cleaned.get("username")
        email = cleaned.get("email")
        if username and user_model.objects.filter(username=username).exists():
            raise ValidationError({"username": _("This username is already in use.")})
        if email and user_model.objects.filter(email=email).exists():
            raise ValidationError({"email": _("This email is already in use.")})
        processor = resolve_processor(cleaned.get("payment_processor"))
        cleaned["payment_processor_instance"] = processor
        return cleaned
