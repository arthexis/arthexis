from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class ChargerVendorSubmission(Entity):
    """Structured public intake for charger vendors seeking suite integration."""

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", _("Pending review")
        IN_REVIEW = "in_review", _("In review")
        FOLLOW_UP = "follow_up", _("Needs follow-up")
        QUALIFIED = "qualified", _("Qualified")
        REJECTED = "rejected", _("Rejected")

    company_name = models.CharField(
        _("Company name"),
        max_length=255,
        help_text=_("Legal entity or business unit responsible for the charger line."),
    )
    contact_name = models.CharField(
        _("Primary contact name"),
        max_length=255,
        help_text=_("Person coordinating technical evaluation and follow-up."),
    )
    contact_email = models.EmailField(
        _("Primary contact email"),
        help_text=_("Email address Arthexis should use for integration follow-up."),
    )
    contact_phone = models.CharField(
        _("Primary contact phone"),
        max_length=64,
        blank=True,
        help_text=_("Optional phone or WhatsApp number for rapid coordination."),
    )
    website = models.URLField(
        _("Company website"),
        blank=True,
        help_text=_("Public website or product landing page for the charger portfolio."),
    )
    charger_brand = models.CharField(
        _("Charger brand"),
        max_length=255,
        help_text=_("Brand presented to operators and end customers."),
    )
    charger_models = models.TextField(
        _("Charger models"),
        help_text=_("List the charger models or SKUs that should be evaluated."),
    )
    ocpp_versions = models.CharField(
        _("Supported OCPP versions"),
        max_length=255,
        help_text=_("For example: OCPP 1.6J, OCPP 2.0.1, proprietary extensions."),
    )
    connectivity_summary = models.TextField(
        _("Connectivity and network summary"),
        help_text=_("Summarize LTE, Ethernet, Wi-Fi, VPN, or SIM/network requirements."),
    )
    api_documentation_url = models.URLField(
        _("API or documentation URL"),
        blank=True,
        help_text=_("Optional link to OCPP notes, APIs, SDKs, or integration guides."),
    )
    certification_summary = models.TextField(
        _("Certification and compliance summary"),
        blank=True,
        help_text=_(
            "Share conformance testing, certifications, and regional compliance notes."
        ),
    )
    deployment_regions = models.CharField(
        _("Deployment regions"),
        max_length=255,
        blank=True,
        help_text=_("Countries or markets where these chargers are currently deployed."),
    )
    deployment_volume = models.CharField(
        _("Installed base"),
        max_length=255,
        blank=True,
        help_text=_("Approximate number of deployed chargers or active sites."),
    )
    remote_access_method = models.TextField(
        _("Remote access and support workflow"),
        help_text=_(
            "Explain how Arthexis could access logs, diagnostics, firmware, or "
            "support tooling."
        ),
    )
    hardware_notes = models.TextField(
        _("Hardware highlights"),
        blank=True,
        help_text=_(
            "Connector types, power ranges, meter support, payment peripherals, "
            "and notable hardware details."
        ),
    )
    integration_goals = models.TextField(
        _("Integration goals"),
        help_text=_(
            "Describe the workflows Arthexis should orchestrate for this charger line."
        ),
    )
    additional_notes = models.TextField(
        _("Additional notes"),
        blank=True,
        help_text=_("Anything else the review team should know before follow-up."),
    )
    review_status = models.CharField(
        _("Review status"),
        max_length=32,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        help_text=_("Internal state used while evaluating the submission."),
    )
    reviewed_at = models.DateTimeField(
        _("Reviewed at"),
        null=True,
        blank=True,
        help_text=_("When the submission was last formally reviewed."),
    )
    reviewed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_charger_vendor_submissions",
        verbose_name=_("Reviewed by"),
        help_text=_("User who completed the latest review step."),
    )

    class Meta:
        db_table = "charger_intake_chargervendorsubmission"
        verbose_name = _("Charger vendor submission")
        verbose_name_plural = _("Charger vendor submissions")
        ordering = ("company_name", "charger_brand", "contact_name")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.company_name} - {self.charger_brand}"

