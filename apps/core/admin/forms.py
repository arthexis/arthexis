from django import forms
from django.apps import apps as django_apps
from django.contrib.auth.forms import UserCreationForm
from django.forms.models import BaseInlineFormSet
from django.utils.translation import gettext_lazy as _
from import_export.forms import (
    ConfirmImportForm,
    ImportForm,
    SelectableFieldsExportForm,
)

from apps.cards.models import RFID
from apps.emails.models import EmailInbox, EmailOutbox
from apps.users.models import User, UserDiagnosticsProfile

from .credential_forms import (
    KeepExistingValue as KeepExistingValue,
)
from .credential_forms import (
    MaskedPasswordFormMixin as MaskedPasswordFormMixin,
)
from .credential_forms import (
    keep_existing as keep_existing,
)
from .rfid_forms import RFIDConfirmImportForm, RFIDExportForm, RFIDImportForm

if django_apps.is_installed("apps.energy"):
    from apps.energy.models import CustomerAccount

if django_apps.is_installed("apps.odoo"):
    from apps.core.widgets import OdooProductWidget
    from apps.odoo.models import OdooEmployee, OdooProduct


class UserCreationWithExpirationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "temporary_expires_at", "site_template")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "temporary_expires_at" in self.fields:
            self.fields["temporary_expires_at"].required = False
        if "site_template" in self.fields:
            self.fields["site_template"].required = False


class UserChangeRFIDForm(forms.ModelForm):
    """Admin change form exposing login RFID assignment."""

    login_rfid = forms.ModelChoiceField(
        label=_("Login RFID"),
        queryset=RFID.objects.none(),
        required=False,
        help_text=_("Assign an RFID card to this user for RFID logins."),
    )

    class Meta:
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance
        field = self.fields["login_rfid"]
        queryset = RFID.objects.all().order_by("label_id")
        if getattr(user, "login_rfid_id", None):
            field.initial = user.login_rfid_id
        field.queryset = queryset
        field.empty_label = _("Keep current assignment")


class UserRFIDWriteForm(forms.Form):
    """Confirm writing RFID login metadata to a card."""

    confirm_write = forms.BooleanField(
        required=True,
        label=_("Confirm write"),
        help_text=_("Write the configured login value to the RFID card."),
    )


if django_apps.is_installed("apps.energy"):

    class CustomerAccountRFIDForm(forms.ModelForm):
        """Form for assigning existing RFIDs to a customer account."""

        class Meta:
            model = CustomerAccount.rfids.through
            fields = ["rfid"]

        def clean_rfid(self):
            rfid = self.cleaned_data["rfid"]
            if rfid.energy_accounts.exclude(
                pk=self.instance.customeraccount_id
            ).exists():
                raise forms.ValidationError(
                    "RFID is already assigned to another customer account"
                )
            return rfid


class UserDiagnosticsProfileInlineForm(forms.ModelForm):
    """Inline admin form for user diagnostics profile settings."""

    class Meta:
        model = UserDiagnosticsProfile
        fields = ("is_enabled", "collect_diagnostics", "allow_manual_feedback")


if django_apps.is_installed("apps.odoo"):

    class OdooEmployeeAdminForm(MaskedPasswordFormMixin, forms.ModelForm):
        """Admin form for :class:`apps.odoo.models.OdooEmployee`."""

        password = forms.CharField(
            required=False,
            help_text="Leave blank to keep the current password.",
        )
        password_field_render_value = True
        password_sigil_fields = ("host", "database", "username", "password")

        class Meta:
            model = OdooEmployee
            fields = "__all__"


class EmailInboxAdminForm(MaskedPasswordFormMixin, forms.ModelForm):
    """Admin form for :class:`apps.emails.models.EmailInbox` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        required=False,
        help_text="Leave blank to keep the current password.",
    )
    password_sigil_fields = ("username", "host", "password", "protocol")

    class Meta:
        model = EmailInbox
        fields = "__all__"


class ProfileInlineFormSet(BaseInlineFormSet):
    """Hide deletion controls and allow implicit removal when empty."""

    @classmethod
    def get_default_prefix(cls):
        prefix = super().get_default_prefix()
        if prefix:
            return prefix
        model_name = cls.model._meta.model_name
        remote_field = getattr(cls.fk, "remote_field", None)
        if remote_field is not None and getattr(remote_field, "one_to_one", False):
            return model_name
        return f"{model_name}_set"

    def add_fields(self, form, index):
        super().add_fields(form, index)
        if "DELETE" in form.fields:
            form.fields["DELETE"].widget = forms.HiddenInput()
            form.fields["DELETE"].required = False


class ProfileFormMixin(forms.ModelForm):
    """Mark profiles for deletion when no data is provided."""

    profile_fields: tuple[str, ...] = ()
    user_datum = forms.BooleanField(
        required=False,
        label=_("User Datum"),
        help_text=_("Store this profile in the user's data directory."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        model_fields = getattr(self._meta.model, "profile_fields", tuple())
        explicit = getattr(self, "profile_fields", tuple())
        self._profile_fields = tuple(explicit or model_fields)
        for name in self._profile_fields:
            field = self.fields.get(name)
            if field is not None:
                field.required = False
        if "user_datum" in self.fields:
            self.fields["user_datum"].initial = getattr(
                self.instance, "is_user_data", False
            )

    @staticmethod
    def _is_empty_value(value) -> bool:
        if isinstance(value, KeepExistingValue):
            return True
        if isinstance(value, bool):
            return not value
        if value in (None, "", [], (), {}, set()):
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def _has_profile_data(self) -> bool:
        for name in self._profile_fields:
            field = self.fields.get(name)
            raw_value = None
            if field is not None and not isinstance(field, forms.BooleanField):
                try:
                    if hasattr(self, "_raw_value"):
                        raw_value = self._raw_value(name)
                    elif self.is_bound:
                        bound = self[name]
                        raw_value = bound.field.widget.value_from_datadict(
                            self.data,
                            self.files,
                            bound.html_name,
                        )
                except (AttributeError, KeyError):
                    raw_value = None
            if raw_value is not None:
                if not isinstance(raw_value, (list, tuple)):
                    values = [raw_value]
                else:
                    values = raw_value
                if any(not self._is_empty_value(value) for value in values):
                    return True
                continue

            if self.is_bound and name not in self.cleaned_data:
                continue

            if name in self.cleaned_data:
                value = self.cleaned_data.get(name)
            elif hasattr(self.instance, name):
                value = getattr(self.instance, name)
            else:
                continue
            if not self._is_empty_value(value):
                return True
        return False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or not self._profile_fields:
            return cleaned
        if not self._has_profile_data():
            cleaned["DELETE"] = True
        return cleaned


if django_apps.is_installed("apps.odoo"):

    class OdooEmployeeInlineForm(ProfileFormMixin, OdooEmployeeAdminForm):
        profile_fields = OdooEmployee.profile_fields

        class Meta(OdooEmployeeAdminForm.Meta):
            exclude = ("user", "group", "verified_on", "odoo_uid", "name", "email")

        def clean(self):
            cleaned = super().clean()
            if cleaned.get("DELETE") or self.errors:
                return cleaned

            provided = [
                name
                for name in self._profile_fields
                if not self._is_empty_value(cleaned.get(name))
            ]
            missing = [
                name
                for name in self._profile_fields
                if self._is_empty_value(cleaned.get(name))
            ]
            if provided and missing:
                raise forms.ValidationError(
                    "Provide host, database, username, and password to create an Odoo employee.",
                )

            return cleaned


class EmailInboxInlineForm(ProfileFormMixin, EmailInboxAdminForm):
    profile_fields = EmailInbox.profile_fields

    class Meta(EmailInboxAdminForm.Meta):
        exclude = ("user", "group")


class EmailOutboxAdminForm(MaskedPasswordFormMixin, forms.ModelForm):
    """Admin form for :class:`apps.emails.models.EmailOutbox` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        required=False,
        help_text="Leave blank to keep the current password.",
    )
    priority = forms.IntegerField(
        required=False,
        initial=0,
        help_text="Higher values are selected first when multiple outboxes are available.",
    )
    password_sigil_fields = ("password", "host", "username", "from_email")

    class Meta:
        model = EmailOutbox
        fields = "__all__"

    def clean_priority(self):
        value = self.cleaned_data.get("priority")
        return 0 if value in (None, "") else value


class EmailOutboxInlineForm(ProfileFormMixin, EmailOutboxAdminForm):
    profile_fields = EmailOutbox.profile_fields

    class Meta(EmailOutboxAdminForm.Meta):
        fields = (
            "password",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
            "is_enabled",
        )


if django_apps.is_installed("apps.odoo"):

    class OdooProductAdminForm(forms.ModelForm):
        class Meta:
            model = OdooProduct
            fields = "__all__"
            widgets = {"odoo_product": OdooProductWidget}
