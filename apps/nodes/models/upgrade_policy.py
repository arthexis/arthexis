from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class UpgradePolicyManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class UpgradePolicy(Entity):
    """Configurable policy governing automated upgrades."""

    class Channel(models.TextChoices):
        STABLE = "stable", _("Stable")
        UNSTABLE = "unstable", _("Unstable")
        LATEST = "latest", _("Latest")

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200, blank=True)
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.STABLE,
    )
    interval_minutes = models.PositiveIntegerField(
        default=10080,
        help_text=_("How often to check for upgrades, in minutes."),
    )
    requires_canaries = models.BooleanField(
        default=False,
        help_text=_("Require configured canaries to be upgraded before proceeding."),
    )
    requires_pypi_packages = models.BooleanField(
        default=False,
        help_text=_("Require the latest PyPI packages before upgrading."),
    )

    objects = UpgradePolicyManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Upgrade Policy"
        verbose_name_plural = "Upgrade Policies"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):
        return (self.name,)


class NodeUpgradePolicyAssignment(Entity):
    """Attach upgrade policy configuration and timing to a node."""

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="upgrade_policy_assignments"
    )
    policy = models.ForeignKey(
        UpgradePolicy,
        on_delete=models.CASCADE,
        related_name="node_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_applied_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=32, blank=True)

    class Meta:
        unique_together = ("node", "policy")
        verbose_name = "Node Upgrade Policy Assignment"
        verbose_name_plural = "Node Upgrade Policy Assignments"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.node} -> {self.policy}"
