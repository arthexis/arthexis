from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.payments.models import OpenPayProcessor, PayPalProcessor, StripeProcessor

from .forms import (
    OpenPayProcessorAdminForm,
    PayPalProcessorAdminForm,
    StripeProcessorAdminForm,
)
from .mixins import SaveBeforeChangeAction, _build_credentials_actions


class PaymentProcessorAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    change_form_template = "django_object_actions/change_form.html"
    readonly_fields = ("verified_on", "verification_reference")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action"]

    @admin.display(description=_("Payment Processor"))
    def display_name(self, obj):
        return obj.identifier()

    def _verify_credentials(self, request, profile):
        identifier = profile.identifier()
        try:
            profile.verify()
        except ValidationError as exc:
            message = "; ".join(exc.messages)
            self.message_user(
                request,
                f"{identifier}: {message}",
                level=messages.ERROR,
            )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request,
                f"{identifier}: {exc}",
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                _("%(name)s verified") % {"name": identifier},
                level=messages.SUCCESS,
            )

    (
        verify_credentials,
        verify_credentials_action,
    ) = _build_credentials_actions("verify_credentials", "_verify_credentials")


@admin.register(OpenPayProcessor)
class OpenPayProcessorAdmin(PaymentProcessorAdmin):
    form = OpenPayProcessorAdminForm
    list_display = ("display_name", "environment", "verified_on")
    fieldsets = (
        (
            _("OpenPay"),
            {
                "fields": (
                    "merchant_id",
                    "public_key",
                    "private_key",
                    "webhook_secret",
                    "is_production",
                ),
                "description": _("Configure OpenPay merchant access."),
            },
        ),
        (
            _("Verification"),
            {"fields": ("verified_on", "verification_reference")},
        ),
    )

    @admin.display(description=_("Environment"))
    def environment(self, obj):
        return _("OpenPay Production") if obj.is_production else _("OpenPay Sandbox")


@admin.register(PayPalProcessor)
class PayPalProcessorAdmin(PaymentProcessorAdmin):
    form = PayPalProcessorAdminForm
    list_display = ("display_name", "environment", "verified_on")
    fieldsets = (
        (
            _("PayPal"),
            {
                "fields": (
                    "client_id",
                    "client_secret",
                    "webhook_id",
                    "is_production",
                ),
                "description": _("Configure PayPal REST API access."),
            },
        ),
        (
            _("Verification"),
            {"fields": ("verified_on", "verification_reference")},
        ),
    )

    @admin.display(description=_("Environment"))
    def environment(self, obj):
        return _("PayPal Production") if obj.is_production else _("PayPal Sandbox")


@admin.register(StripeProcessor)
class StripeProcessorAdmin(PaymentProcessorAdmin):
    form = StripeProcessorAdminForm
    list_display = ("display_name", "environment", "verified_on")
    fieldsets = (
        (
            _("Stripe"),
            {
                "fields": (
                    "secret_key",
                    "publishable_key",
                    "webhook_secret",
                    "is_production",
                ),
                "description": _("Configure Stripe API access."),
            },
        ),
        (
            _("Verification"),
            {"fields": ("verified_on", "verification_reference")},
        ),
    )

    @admin.display(description=_("Environment"))
    def environment(self, obj):
        return _("Stripe Live") if obj.is_production else _("Stripe Test")
