import uuid

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from import_export.forms import (
    ConfirmImportForm,
    ImportForm,
    SelectableFieldsExportForm,
)

from apps.cards.models import RFID
from apps.emails.models import EmailInbox, EmailOutbox
from apps.energy.models import CustomerAccount
from apps.odoo.models import OdooEmployee, OdooProduct
from apps.payments.models import OpenPayProcessor, PayPalProcessor, StripeProcessor
from apps.users.models import User
from apps.core.widgets import OdooProductWidget


def _raw_instance_value(instance, field_name):
    """Return the stored value for ``field_name`` without resolving sigils."""

    field = instance._meta.get_field(field_name)
    if not instance.pk:
        return field.value_from_object(instance)
    manager = type(instance)._default_manager
    try:
        return (
            manager.filter(pk=instance.pk).values_list(field.attname, flat=True).get()
        )
    except type(instance).DoesNotExist:  # pragma: no cover - instance deleted
        return field.value_from_object(instance)


class KeepExistingValue:
    """Sentinel indicating a field should retain its stored value."""

    __slots__ = ("field",)

    def __init__(self, field: str):
        self.field = field

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return False

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<KeepExistingValue field={self.field!r}>"


def keep_existing(field: str) -> KeepExistingValue:
    return KeepExistingValue(field)


def _restore_sigil_values(form, field_names):
    """Reset sigil fields on ``form.instance`` to their raw form values."""

    for name in field_names:
        if name not in form.fields:
            continue
        if name in form.cleaned_data:
            raw = form.cleaned_data[name]
            if isinstance(raw, KeepExistingValue):
                raw = _raw_instance_value(form.instance, name)
        else:
            raw = _raw_instance_value(form.instance, name)
        setattr(form.instance, name, raw)


class CustomerAccountRFIDForm(forms.ModelForm):
    """Form for assigning existing RFIDs to a customer account."""

    class Meta:
        model = CustomerAccount.rfids.through
        fields = ["rfid"]

    def clean_rfid(self):
        rfid = self.cleaned_data["rfid"]
        if rfid.energy_accounts.exclude(pk=self.instance.customeraccount_id).exists():
            raise forms.ValidationError(
                "RFID is already assigned to another customer account"
            )
        return rfid


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
        account = getattr(user, "customer_account", None)
        if account is not None:
            queryset = RFID.objects.filter(
                Q(energy_accounts__isnull=True) | Q(energy_accounts=account)
            )
            current = account.rfids.order_by("label_id").first()
            if current:
                field.initial = current.pk
        else:
            queryset = RFID.objects.filter(energy_accounts__isnull=True)
        field.queryset = queryset.order_by("label_id")
        field.empty_label = _("Keep current assignment")

    def _ensure_customer_account(self, user):
        account = getattr(user, "customer_account", None)
        if account is not None:
            if account.user_id != user.pk:
                account.user = user
                account.save(update_fields=["user"])
            return account
        account = CustomerAccount.objects.filter(user=user).first()
        if account is not None:
            if account.user_id != user.pk:
                account.user = user
                account.save(update_fields=["user"])
            return account
        base_slug = slugify(
            user.username
            or user.get_full_name()
            or user.email
            or (str(user.pk) if user.pk is not None else "")
        )
        if not base_slug:
            base_slug = f"user-{uuid.uuid4().hex[:8]}"
        base_name = base_slug.upper()
        candidate = base_name
        suffix = 1
        while CustomerAccount.objects.filter(name=candidate).exists():
            suffix += 1
            candidate = f"{base_name}-{suffix}"
        return CustomerAccount.objects.create(user=user, name=candidate)

    def save(self, commit=True):
        user = super().save(commit)
        rfid = self.cleaned_data.get("login_rfid")
        if not rfid:
            return user
        account = self._ensure_customer_account(user)
        if account.pk is None:
            account.save()
        other_accounts = list(rfid.energy_accounts.exclude(pk=account.pk))
        if other_accounts:
            rfid.energy_accounts.remove(*other_accounts)
        if not account.rfids.filter(pk=rfid.pk).exists():
            account.rfids.add(rfid)
        return user


class OdooEmployeeAdminForm(forms.ModelForm):
    """Admin form for :class:`core.models.OdooEmployee` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = OdooEmployee
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["password"].initial = ""
            self.initial["password"] = ""
        else:
            self.fields["password"].required = True

    def clean_password(self):
        pwd = self.cleaned_data.get("password")
        if not pwd and self.instance.pk:
            return keep_existing("password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            ["host", "database", "username", "password"],
        )


class PaymentProcessorAdminForm(forms.ModelForm):
    masked_fields: tuple[str, ...] = ()
    sigil_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            for field in self.masked_fields:
                if field in self.fields:
                    self.fields[field].initial = ""
                    self.initial[field] = ""

    @staticmethod
    def _has_value(value) -> bool:
        if isinstance(value, KeepExistingValue):
            return True
        if isinstance(value, bool):
            return value
        return value not in (None, "", [], (), {}, set())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        if self.instance.pk:
            for field in self.masked_fields:
                if cleaned.get(field) == "":
                    cleaned[field] = keep_existing(field)
        return cleaned

    def _post_clean(self):
        super()._post_clean()
        if self.sigil_fields:
            _restore_sigil_values(self, list(self.sigil_fields))


class OpenPayProcessorAdminForm(PaymentProcessorAdminForm):
    masked_fields = ("private_key", "webhook_secret")
    sigil_fields = ("merchant_id", "private_key", "public_key", "webhook_secret")

    class Meta:
        model = OpenPayProcessor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["merchant_id"].help_text = _(
            "Provide merchant ID, public and private keys, and webhook secret from OpenPay."
        )
        self.fields["public_key"].help_text = _(
            "OpenPay public key used for browser integrations."
        )
        self.fields["private_key"].help_text = _(
            "OpenPay private key used for server-side requests. Leave blank to keep the current key."
        )
        self.fields["webhook_secret"].help_text = _(
            "Secret used to sign OpenPay webhooks. Leave blank to keep the current secret."
        )
        self.fields["is_production"].help_text = _(
            "Enable to send requests to OpenPay's live environment."
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned

        required = ("merchant_id", "private_key", "public_key")
        provided = [name for name in required if self._has_value(cleaned.get(name))]
        missing = [name for name in required if not self._has_value(cleaned.get(name))]
        if provided and missing:
            raise forms.ValidationError(
                _("Provide merchant ID, private key, and public key to configure OpenPay.")
            )
        if not provided:
            raise forms.ValidationError(
                _("Provide merchant ID, private key, and public key to configure OpenPay.")
            )
        return cleaned


class PayPalProcessorAdminForm(PaymentProcessorAdminForm):
    masked_fields = ("client_secret",)
    sigil_fields = ("client_id", "client_secret", "webhook_id")

    class Meta:
        model = PayPalProcessor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client_id"].help_text = _("PayPal REST client ID for your application.")
        self.fields["client_secret"].help_text = _(
            "PayPal REST client secret. Leave blank to keep the current secret."
        )
        self.fields["webhook_id"].help_text = _(
            "PayPal webhook ID used to validate notifications. Leave blank to keep the current webhook identifier."
        )
        self.fields["is_production"].help_text = _(
            "Enable to send requests to PayPal's live environment."
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned
        required = ("client_id", "client_secret")
        provided = [name for name in required if self._has_value(cleaned.get(name))]
        if len(provided) != len(required):
            raise forms.ValidationError(
                _("Provide PayPal client ID and client secret to configure PayPal.")
            )
        return cleaned


class StripeProcessorAdminForm(PaymentProcessorAdminForm):
    masked_fields = ("secret_key", "webhook_secret")
    sigil_fields = ("secret_key", "publishable_key", "webhook_secret")

    class Meta:
        model = StripeProcessor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secret_key"].help_text = _(
            "Stripe secret key used for authenticated API requests. Leave blank to keep the current key."
        )
        self.fields["publishable_key"].help_text = _(
            "Stripe publishable key used by client integrations."
        )
        self.fields["webhook_secret"].help_text = _(
            "Secret used to validate Stripe webhook signatures. Leave blank to keep the current secret."
        )
        self.fields["is_production"].help_text = _(
            "Enable to mark Stripe as live mode; disable for test mode."
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned
        required = ("secret_key", "publishable_key")
        provided = [name for name in required if self._has_value(cleaned.get(name))]
        if len(provided) != len(required):
            raise forms.ValidationError(
                _("Provide Stripe secret and publishable keys to configure Stripe.")
            )
        return cleaned


class MaskedPasswordFormMixin:
    """Mixin that hides stored passwords while allowing updates."""

    password_sigil_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("password")
        if field is None:
            return
        if not isinstance(field.widget, forms.PasswordInput):
            field.widget = forms.PasswordInput()
        field.widget.attrs.setdefault("autocomplete", "new-password")
        field.help_text = field.help_text or "Leave blank to keep the current password."
        if self.instance.pk:
            field.initial = ""
            self.initial["password"] = ""
        else:
            field.required = True

    def clean_password(self):
        field = self.fields.get("password")
        if field is None:
            return self.cleaned_data.get("password")
        pwd = self.cleaned_data.get("password")
        if not pwd and self.instance.pk:
            return keep_existing("password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        if self.password_sigil_fields:
            _restore_sigil_values(self, self.password_sigil_fields)


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


class RFIDImportForm(ImportForm):
    account_field = forms.ChoiceField(
        choices=(
            ("id", _("Energy account IDs")),
            ("name", _("Energy account names")),
        ),
        initial="id",
        label=_("Energy accounts"),
        required=False,
    )

    field_order = ["resource", "import_file", "format", "account_field"]

    def __init__(self, formats, resources, **kwargs):
        super().__init__(formats, resources, **kwargs)
        self.fields["account_field"].initial = (
            self.data.get("account_field")
            if hasattr(self, "data") and self.data
            else "id"
        )


class RFIDExportForm(SelectableFieldsExportForm):
    account_field = forms.ChoiceField(
        choices=(
            ("id", _("Energy account IDs")),
            ("name", _("Energy account names")),
        ),
        initial="id",
        label=_("Energy accounts"),
        required=False,
    )

    field_order = ["resource", "format", "account_field"]

    def __init__(self, formats, resources, **kwargs):
        super().__init__(formats, resources, **kwargs)
        if hasattr(self, "data") and self.data:
            self.fields["account_field"].initial = self.data.get("account_field", "id")


class RFIDConfirmImportForm(ConfirmImportForm):
    account_field = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_account_field(self):
        value = (self.cleaned_data.get("account_field") or "id").lower()
        if value not in {"id", "name"}:
            return "id"
        return value


class OdooProductAdminForm(forms.ModelForm):
    class Meta:
        model = OdooProduct
        fields = "__all__"
        widgets = {"odoo_product": OdooProductWidget}
