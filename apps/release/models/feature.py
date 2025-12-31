from __future__ import annotations

from packaging.version import InvalidVersion, Version

from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity, EntityManager, EntityQuerySet

from .package import Package


class FeatureQuerySet(EntityQuerySet):
    def due_for_version(self, version: str | None) -> "FeatureQuerySet":
        """Return features expected by the provided version."""

        if not version:
            return self.none()
        try:
            target = Version(version)
        except InvalidVersion:
            return self.none()
        feature_ids: list[int] = []
        for feature in self.all():
            try:
                expected = Version(feature.expected_version)
            except InvalidVersion:
                continue
            if expected <= target:
                feature_ids.append(feature.pk)
        return self.filter(pk__in=feature_ids)


class FeatureManager(EntityManager):
    def get_queryset(self):
        return FeatureQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def get_by_natural_key(self, package, slug):
        return self.get(package__name=package, slug=slug)


class Feature(Entity):
    """Release feature expectations for a package version."""

    objects = FeatureManager()

    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name="features"
    )
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=255)
    summary = models.CharField(max_length=255, blank=True, default="")
    expected_version = models.CharField(
        max_length=20,
        help_text=_("Version where the feature must be available."),
    )
    scope = models.TextField(
        blank=True,
        default="",
        help_text=_("Applications, commands or interfaces covered by this feature."),
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text=_(
            "Markdown/mermaid/pseudo-code or design notes narrowing down the feature"
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Feature"
        verbose_name_plural = "Features"
        constraints = [
            models.UniqueConstraint(
                fields=("package", "slug"), name="unique_package_feature_slug"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.package}:{self.slug}"

    def natural_key(self):
        return (self.package.name, self.slug)

    def clean(self):
        super().clean()
        if not self.slug:
            self.slug = slugify(self.name)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def is_due_for_version(self, version: str | None) -> bool:
        """Return True when the feature should be satisfied in ``version``."""

        if not version:
            return False
        try:
            target = Version(version)
            expected = Version(self.expected_version)
        except InvalidVersion:
            return False
        return expected <= target


class FeatureArtifact(Entity):
    """Artifacts attached to a feature definition."""

    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="artifacts"
    )
    label = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(
        blank=True,
        default="",
        help_text=_("Markdown or diagram snippets for this artifact."),
    )
    attachment = models.FileField(
        upload_to="release/features/", blank=True, null=True
    )

    class Meta:
        verbose_name = "Feature artifact"
        verbose_name_plural = "Feature artifacts"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.label or self.feature.name


class FeatureTestCase(Entity):
    """Track tests associated with a release feature."""

    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="test_cases"
    )
    test_node_id = models.CharField(max_length=512)
    test_name = models.CharField(max_length=255)
    last_status = models.CharField(max_length=16, blank=True, default="")
    last_duration = models.FloatField(null=True, blank=True)
    last_log = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Feature test case"
        verbose_name_plural = "Feature test cases"
        constraints = [
            models.UniqueConstraint(
                fields=("feature", "test_node_id"),
                name="unique_feature_test_node",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.feature.slug}: {self.test_name}"
