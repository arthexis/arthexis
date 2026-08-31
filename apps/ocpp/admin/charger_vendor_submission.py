from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.ocpp.models import ChargerVendorSubmission


@admin.register(ChargerVendorSubmission)
class ChargerVendorSubmissionAdmin(EntityModelAdmin):
    """Admin review surface for public charger vendor intake submissions."""

    list_display = (
        "company_name",
        "charger_brand",
        "contact_name",
        "contact_email",
        "review_status",
        "reviewed_at",
        "reviewed_by",
    )
    list_filter = ("review_status", "reviewed_at")
    search_fields = (
        "company_name",
        "charger_brand",
        "contact_name",
        "contact_email",
        "charger_models",
        "integration_goals",
    )
    raw_id_fields = ("reviewed_by",)

