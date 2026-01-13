from __future__ import annotations

from dataclasses import dataclass
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.media.utils import create_media_file

from .models import (
    Term,
    TermAcceptance,
    ensure_terms_acceptance_bucket,
    ensure_terms_registration_photo_bucket,
    matches_patterns,
)


@dataclass(frozen=True)
class TermFieldConfig:
    term: Term
    accept_field: str
    document_field: str | None


class TermAcceptanceForm(forms.Form):
    def __init__(self, term: Term, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.term = term
        self.fields["accept"] = forms.BooleanField(label=term.checkbox_text)
        if term.requires_document:
            label = term.required_document_label or _("Required document")
            self.fields["document"] = forms.FileField(label=label)

    def clean_document(self):
        document = self.cleaned_data.get("document")
        term = self.term
        if not term.requires_document:
            return document
        errors = _validate_term_document(
            term,
            document,
            required_message=_("This document is required."),
        )
        if errors:
            raise ValidationError(errors)
        return document

    def save(self, *, user=None, submission=None, ip_address="", user_agent=""):
        document_file = self.cleaned_data.get("document")
        document_media = None
        if document_file:
            bucket = ensure_terms_acceptance_bucket()
            document_media = create_media_file(
                bucket=bucket,
                uploaded_file=document_file,
            )
        return TermAcceptance.objects.create(
            term=self.term,
            user=user,
            submission=submission,
            checkbox_text=self.term.checkbox_text,
            ip_address=ip_address,
            user_agent=user_agent,
            required_document_media=document_media,
        )


class RegistrationForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)
    photo = forms.ImageField(label=_("Profile photo"))

    def __init__(self, terms: list[Term], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.terms = terms
        self.term_fields: list[TermFieldConfig] = []
        for term in terms:
            accept_field = f"accept_{term.pk}"
            self.fields[accept_field] = forms.BooleanField(label=term.checkbox_text)
            document_field = None
            if term.requires_document:
                document_field = f"document_{term.pk}"
                label = term.required_document_label or _("Required document")
                self.fields[document_field] = forms.FileField(label=label)
            self.term_fields.append(
                TermFieldConfig(
                    term=term,
                    accept_field=accept_field,
                    document_field=document_field,
                )
            )

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm = cleaned_data.get("confirm_password")
        if password and confirm and password != confirm:
            self.add_error("confirm_password", _("Passwords do not match."))
        if password:
            validate_password(password)
        self.clean_term_documents()
        return cleaned_data

    def clean_username(self):
        username = self.cleaned_data.get("username")
        user_model = get_user_model()
        if username and user_model.all_objects.filter(username=username).exists():
            raise ValidationError(_("This username is already taken."))
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email")
        user_model = get_user_model()
        if email and user_model.all_objects.filter(email=email).exists():
            raise ValidationError(_("This email is already registered."))
        return email

    def clean_term_documents(self):
        for config in self.term_fields:
            term = config.term
            if not config.document_field:
                continue
            document = self.cleaned_data.get(config.document_field)
            errors = _validate_term_document(
                term,
                document,
                required_message=_("A required document is missing."),
            )
            for message in errors:
                self.add_error(config.document_field, message)

    def save_photo(self):
        photo_file = self.cleaned_data.get("photo")
        bucket = ensure_terms_registration_photo_bucket()
        return create_media_file(bucket=bucket, uploaded_file=photo_file)

    def save_user(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username=self.cleaned_data["username"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password"],
        )
        user.is_active = False
        user.save(update_fields=["is_active"])
        return user

    def cleaned_document_for(self, term: Term):
        field_name = f"document_{term.pk}"
        if field_name in self.fields:
            return self.cleaned_data.get(field_name)
        return None


def _validate_term_document(
    term: Term,
    document,
    *,
    required_message: str,
) -> list[str]:
    if not document:
        return [required_message]
    errors = []
    if term.required_document_patterns and not matches_patterns(
        getattr(document, "name", ""), term.required_document_patterns
    ):
        errors.append(_("This file type is not allowed."))
    size = getattr(document, "size", 0) or 0
    if term.required_document_min_bytes and size < term.required_document_min_bytes:
        errors.append(_("This file is too small."))
    if term.required_document_max_bytes and size > term.required_document_max_bytes:
        errors.append(_("This file is too large."))
    return errors
