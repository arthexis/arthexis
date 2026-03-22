from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from parler.models import TranslatableModel, TranslatedFields

from apps.core.entity import Entity


class ContentSample(Entity):
    """Collected content such as text snippets or screenshots."""

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    KIND_CHOICES = [(TEXT, "Text"), (IMAGE, "Image"), (AUDIO, "Audio")]

    name = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    content = models.TextField(blank=True)
    path = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=10, default="", blank=True)
    hash = models.CharField(max_length=64, blank=True)
    transaction_uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=True,
        db_index=True,
        verbose_name="transaction UUID",
    )
    node = models.ForeignKey(
        "nodes.Node", on_delete=models.SET_NULL, null=True, blank=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Content Sample"
        verbose_name_plural = "Content Samples"
        constraints = [
            models.UniqueConstraint(
                fields=["hash"],
                condition=~Q(hash=""),
                name="nodes_contentsample_hash_unique",
            )
        ]
        db_table = "nodes_contentsample"

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.get(pk=self.pk)
            if original.transaction_uuid != self.transaction_uuid:
                raise ValidationError(
                    {"transaction_uuid": "Cannot modify transaction UUID"}
                )
        if self.node_id is None:
            from apps.nodes.models import Node

            self.node = Node.get_local()
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return str(self.name)


class ContentClassifier(TranslatableModel, Entity):
    """Configured callable that classifies :class:`ContentSample` objects."""

    slug = models.SlugField(max_length=100, unique=True)
    translations = TranslatedFields(
        label=models.CharField(max_length=150),
    )
    kind = models.CharField(max_length=10, choices=ContentSample.KIND_CHOICES)
    entrypoint = models.CharField(max_length=255, help_text="Dotted path to classifier callable")
    run_by_default = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["translations__label"]
        verbose_name = "Content Classifier"
        verbose_name_plural = "Content Classifiers"
        db_table = "nodes_contentclassifier"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.safe_translation_getter("label", any_language=True) or self.slug


class ContentTag(TranslatableModel, Entity):
    """Tag that can be attached to classified content samples."""

    slug = models.SlugField(max_length=100, unique=True)
    translations = TranslatedFields(
        label=models.CharField(max_length=150),
    )

    class Meta:
        ordering = ["translations__label"]
        verbose_name = "Content Tag"
        verbose_name_plural = "Content Tags"
        db_table = "nodes_contenttag"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.safe_translation_getter("label", any_language=True) or self.slug


class ContentClassification(Entity):
    """Link between a sample, classifier, and assigned tag."""

    sample = models.ForeignKey(
        ContentSample, on_delete=models.CASCADE, related_name="classifications"
    )
    classifier = models.ForeignKey(
        ContentClassifier, on_delete=models.CASCADE, related_name="classifications"
    )
    tag = models.ForeignKey(
        ContentTag, on_delete=models.CASCADE, related_name="classifications"
    )
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("sample", "classifier", "tag")
        ordering = ["-created_at"]
        verbose_name = "Content Classification"
        verbose_name_plural = "Content Classifications"
        db_table = "nodes_contentclassification"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.sample} → {self.tag}"


class WebSample(Entity):
    """Historical record captured by a retired web sampler configuration."""

    legacy_sampler_id = models.PositiveIntegerField(null=True, blank=True)
    sampler_slug = models.SlugField(max_length=100, blank=True)
    sampler_label = models.CharField(max_length=150, blank=True)
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="web_samples",
    )
    document = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Historical Web Sample"
        verbose_name_plural = "Historical Web Samples"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.sampler_label or self.sampler_slug or "web sample"
        return f"{label} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class WebSampleAttachment(Entity):
    """Read-only attachment metadata preserved from a historical web sample."""

    sample = models.ForeignKey(
        WebSample, on_delete=models.CASCADE, related_name="attachments"
    )
    legacy_step_id = models.PositiveIntegerField(null=True, blank=True)
    step_slug = models.SlugField(max_length=100, blank=True)
    step_name = models.CharField(max_length=150, blank=True)
    uri = models.CharField(max_length=500)
    content_sample = models.ForeignKey(
        ContentSample, on_delete=models.CASCADE, related_name="web_attachments"
    )

    class Meta:
        verbose_name = "Historical Web Sample Attachment"
        verbose_name_plural = "Historical Web Sample Attachments"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Attachment for {self.sample}"
