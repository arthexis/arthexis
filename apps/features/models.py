from __future__ import annotations

from django.apps import apps as django_apps
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable
from apps.core.entity import Entity


class FeatureManager(models.Manager):
    def get_by_natural_key(self, slug: str):  # pragma: no cover - used by fixtures
        return self.get(slug=slug)


class Feature(Ownable):
    """Suite feature definitions that map to tests and runtime gating."""

    owner_required = False

    slug = models.SlugField(max_length=120, unique=True)
    display = models.CharField(max_length=120)
    summary = models.TextField(blank=True)
    is_enabled = models.BooleanField(
        default=True,
        help_text=_(
            "Global gate for this feature. Disable to block the feature everywhere."
        ),
    )
    main_app = models.ForeignKey(
        "app.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="features",
        help_text=_(
            "Primary application that owns most of the implementation for this feature."
        ),
    )
    node_feature = models.ForeignKey(
        "nodes.NodeFeature",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suite_features",
        help_text=_(
            "Optional node feature that must be enabled for this feature to unlock."
        ),
    )
    admin_requirements = models.TextField(
        blank=True,
        help_text=_("Admin-side capabilities, screens, and workflows."),
    )
    public_requirements = models.TextField(
        blank=True,
        help_text=_("Public-facing UI/UX requirements for this feature."),
    )
    service_requirements = models.TextField(
        blank=True,
        help_text=_("Non-user surfaces such as APIs, webhooks, or websockets."),
    )
    admin_views = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Admin views or URLs used to deliver this feature."),
    )
    public_views = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Public views or URLs used to deliver this feature."),
    )
    service_views = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Non-user endpoints such as APIs, sockets, or background workers."),
    )
    code_locations = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Relevant code modules, files, or packages for this feature."),
    )
    protocol_coverage = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Protocol call coverage keyed by protocol slug (e.g. ocpp16, ocpp201, ocpp21)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FeatureManager()

    class Meta:
        ordering = ["display"]
        verbose_name = "Feature"
        verbose_name_plural = "Features"

    def natural_key(self):  # pragma: no cover - used by fixtures
        return (self.slug,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display

    def get_absolute_url(self):
        return reverse("features:detail", kwargs={"slug": self.slug})

    def is_enabled_for_node(self, node=None) -> bool:
        """Return whether the feature is enabled for the supplied node."""
        if not self.is_enabled:
            return False
        if not self.node_feature_id:
            return True
        try:
            NodeModel = django_apps.get_model("nodes", "Node")
        except LookupError:
            return False
        if node is None:
            node = NodeModel.get_local()
        if not node:
            return False
        return node.features.filter(pk=self.node_feature_id).exists()


class FeatureTestManager(models.Manager):
    def get_by_natural_key(self, feature_slug: str, node_id: str):  # pragma: no cover
        return self.select_related("feature").get(
            feature__slug=feature_slug,
            node_id=node_id,
        )


class FeatureTest(Entity):
    """Track tests that guard a feature from regressions."""

    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        related_name="tests",
    )
    node_id = models.CharField(max_length=512, help_text="Full pytest node identifier")
    name = models.CharField(max_length=255, help_text="Short test name")
    is_regression_guard = models.BooleanField(
        default=True,
        help_text=_("Marks this test as a required regression guard for the feature."),
    )
    notes = models.TextField(blank=True)

    objects = FeatureTestManager()

    class Meta:
        ordering = ["feature", "node_id"]
        unique_together = ("feature", "node_id")
        verbose_name = "Feature test"
        verbose_name_plural = "Feature tests"

    def natural_key(self):  # pragma: no cover - used by fixtures
        return (*self.feature.natural_key(), self.node_id)

    natural_key.dependencies = ["features.feature"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.feature.display}: {self.name}"


class FeatureNote(Entity):
    """Track developer commentary for a feature over time."""

    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feature_notes",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]
        verbose_name = "Feature note"
        verbose_name_plural = "Feature notes"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        snippet = (self.body or "").strip()
        if len(snippet) > 80:
            snippet = f"{snippet[:77]}..."
        return f"{self.feature.display}: {snippet}"


__all__ = ["Feature", "FeatureNote", "FeatureTest"]
