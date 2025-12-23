from django.contrib import admin

from apps.emails.models import EmailCollector, EmailInbox, EmailOutbox
from apps.energy.models import CustomerAccount
from apps.odoo.models import OdooEmployee
from apps.users.models import UserPhoneNumber

from .forms import (
    CustomerAccountRFIDForm,
    EmailInboxInlineForm,
    EmailOutboxInlineForm,
    OdooEmployeeInlineForm,
    ProfileInlineFormSet,
)


def _title_case(value):
    text = str(value or "")
    return " ".join(
        word[:1].upper() + word[1:] if word else word for word in text.split()
    )


PROFILE_INLINE_CONFIG = {
    OdooEmployee: {
        "form": OdooEmployeeInlineForm,
        "fieldsets": (
            (
                None,
                {
                    "fields": (
                        "host",
                        "database",
                        "username",
                        "password",
                    )
                },
            ),
            (
                "Odoo Employee",
                {
                    "fields": ("verified_on", "odoo_uid", "name", "email"),
                },
            ),
        ),
        "readonly_fields": ("verified_on", "odoo_uid", "name", "email"),
    },
    EmailInbox: {
        "form": EmailInboxInlineForm,
        "fields": (
            "username",
            "host",
            "port",
            "password",
            "protocol",
            "use_ssl",
            "is_enabled",
            "priority",
        ),
    },
    EmailOutbox: {
        "form": EmailOutboxInlineForm,
        "fields": (
            "password",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
        ),
    },
}


def _build_profile_inline(model, owner_field):
    config = PROFILE_INLINE_CONFIG[model]
    verbose_name = config.get("verbose_name")
    if verbose_name is None:
        verbose_name = _title_case(model._meta.verbose_name)
    verbose_name_plural = config.get("verbose_name_plural")
    if verbose_name_plural is None:
        verbose_name_plural = _title_case(model._meta.verbose_name_plural)
    attrs = {
        "model": model,
        "fk_name": owner_field,
        "form": config["form"],
        "formset": ProfileInlineFormSet,
        "extra": 1,
        "max_num": 1,
        "can_delete": True,
        "verbose_name": verbose_name,
        "verbose_name_plural": verbose_name_plural,
        "template": "admin/edit_inline/profile_stacked.html",
        "fieldset_visibility": tuple(config.get("fieldset_visibility", ())),
    }
    if "fieldsets" in config:
        attrs["fieldsets"] = config["fieldsets"]
    if "fields" in config:
        attrs["fields"] = config["fields"]
    if "readonly_fields" in config:
        attrs["readonly_fields"] = config["readonly_fields"]
    if "template" in config:
        attrs["template"] = config["template"]
    return type(
        f"{model.__name__}{owner_field.title()}Inline",
        (admin.StackedInline,),
        attrs,
    )


PROFILE_MODELS = (
    OdooEmployee,
    EmailInbox,
    EmailOutbox,
)
USER_PROFILE_INLINES = [
    _build_profile_inline(model, "user") for model in PROFILE_MODELS
]
GROUP_PROFILE_INLINES = [
    _build_profile_inline(model, "group") for model in PROFILE_MODELS
]


class UserPhoneNumberInline(admin.TabularInline):
    model = UserPhoneNumber
    extra = 0
    fields = ("number", "priority")


class EmailCollectorInline(admin.TabularInline):
    model = EmailCollector
    extra = 0
    fields = ("name", "subject", "sender")


class CustomerAccountRFIDInline(admin.TabularInline):
    model = CustomerAccount.rfids.through
    form = CustomerAccountRFIDForm
    autocomplete_fields = ["rfid"]
    extra = 0
    verbose_name = "RFID"
    verbose_name_plural = "RFIDs"
