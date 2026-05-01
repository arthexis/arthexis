from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


def default_card_session_id() -> str:
    return uuid.uuid4().hex


class SoulIntent(Entity):
    """Operator-entered intent used to select or compose a card-bound agent surface."""

    class RiskLevel(models.TextChoices):
        LOW = "low", _("Low")
        MEDIUM = "medium", _("Medium")
        HIGH = "high", _("High")

    class InterfaceMode(models.TextChoices):
        AUTO = "auto", _("Auto")
        CLI = "cli", _("CLI")
        WEB = "web", _("Web")

    problem_statement = models.TextField()
    normalized_intent = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    role = models.CharField(max_length=64, blank=True, default="")
    constraints = models.JSONField(default=dict, blank=True)
    risk_level = models.CharField(max_length=16, choices=RiskLevel.choices, default=RiskLevel.MEDIUM)
    desired_interface = models.CharField(
        max_length=16,
        choices=InterfaceMode.choices,
        default=InterfaceMode.AUTO,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="soul_intents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id",)
        verbose_name = _("Soul Intent")
        verbose_name_plural = _("Soul Intents")

    def __str__(self) -> str:
        return (self.normalized_intent or self.problem_statement)[:80]


class SkillBundle(Entity):
    """Approved set of skills selected for one Soul Seed card intent."""

    class MatchStrategy(models.TextChoices):
        EXACT = "exact", _("Exact")
        COMPOSED = "composed", _("Composed")
        MANUAL = "manual", _("Manual")

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=120, unique=True)
    intent = models.ForeignKey(
        SoulIntent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="skill_bundles",
    )
    primary_skill = models.ForeignKey(
        "skills.AgentSkill",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_soul_seed_bundles",
    )
    skills = models.ManyToManyField(
        "skills.AgentSkill",
        blank=True,
        related_name="soul_seed_bundles",
    )
    match_strategy = models.CharField(
        max_length=16,
        choices=MatchStrategy.choices,
        default=MatchStrategy.COMPOSED,
    )
    match_score = models.FloatField(default=0.0)
    summary = models.TextField(blank=True, default="")
    tool_allowlist = models.JSONField(default=list, blank=True)
    compatibility_notes = models.JSONField(default=list, blank=True)
    fallback_guidance = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)
        verbose_name = _("Skill Bundle")
        verbose_name_plural = _("Skill Bundles")

    def __str__(self) -> str:
        return self.slug


class AgentInterfaceSpec(Entity):
    """Suite-renderable UI/CLI contract for a card-bound agent session."""

    class Mode(models.TextChoices):
        AUTO = "auto", _("Auto")
        CLI = "cli", _("CLI")
        WEB = "web", _("Web")

    bundle = models.ForeignKey(
        SkillBundle,
        on_delete=models.CASCADE,
        related_name="interface_specs",
    )
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.AUTO)
    schema = models.JSONField(default=dict, blank=True)
    commands = models.JSONField(default=list, blank=True)
    suggestions = models.JSONField(default=list, blank=True)
    visible_fields = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("bundle__slug", "mode", "-id")
        verbose_name = _("Agent Interface Spec")
        verbose_name_plural = _("Agent Interface Specs")

    def __str__(self) -> str:
        return f"{self.bundle_id}:{self.mode}"


class SoulSeedCard(Entity):
    """Registry record for a physical card that points to suite-owned intent data."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        PREVIEW_ONLY = "preview_only", _("Preview only")
        REVOKED = "revoked", _("Revoked")

    rfid = models.ForeignKey(
        "cards.RFID",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="soul_seed_cards",
    )
    card_uid = models.CharField(max_length=255, db_index=True)
    manifest_fingerprint = models.CharField(max_length=64, blank=True, default="", db_index=True)
    intent = models.ForeignKey(
        SoulIntent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="soul_seed_cards",
    )
    skill_bundle = models.ForeignKey(
        SkillBundle,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="soul_seed_cards",
    )
    interface_spec = models.ForeignKey(
        AgentInterfaceSpec,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="soul_seed_cards",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="soul_seed_cards",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    card_payload = models.JSONField(default=dict, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id",)
        verbose_name = _("Soul Seed Card")
        verbose_name_plural = _("Soul Seed Cards")

    def __str__(self) -> str:
        return self.card_uid


class CardSession(Entity):
    """Ephemeral activation record for one card at one suite-enabled console."""

    class State(models.TextChoices):
        PLANNED = "planned", _("Planned")
        ACTIVE = "active", _("Active")
        CLOSED = "closed", _("Closed")
        EVICTED = "evicted", _("Evicted")
        REJECTED = "rejected", _("Rejected")

    class TrustTier(models.TextChoices):
        UNKNOWN = "unknown", _("Unknown")
        LOCAL_AUTHENTICATED = "local_authenticated", _("Local authenticated")
        TRUSTED_OPERATOR_CONSOLE = "trusted_operator_console", _("Trusted operator console")
        TRUSTED_GWAY = "trusted_gway", _("Trusted GWAY")
        PROVISIONER = "provisioner", _("Provisioner")

    session_id = models.CharField(max_length=64, unique=True, default=default_card_session_id)
    card = models.ForeignKey(
        SoulSeedCard,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sessions",
    )
    rfid = models.ForeignKey(
        "cards.RFID",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="soul_seed_sessions",
    )
    reader_id = models.CharField(max_length=128, blank=True, default="")
    node_id = models.CharField(max_length=128, blank=True, default="")
    trust_tier = models.CharField(max_length=32, choices=TrustTier.choices, default=TrustTier.UNKNOWN)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PLANNED)
    activation_plan = models.JSONField(default=dict, blank=True)
    runtime_namespace = models.CharField(max_length=128, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    eviction_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id",)
        verbose_name = _("Card Session")
        verbose_name_plural = _("Card Sessions")

    def __str__(self) -> str:
        return self.session_id
