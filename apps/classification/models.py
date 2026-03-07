"""Domain models for image classifier training and orchestration."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.entity import Entity


class ClassificationTag(Entity):
    """Taxonomy tag used to label ingested content."""

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    auto_dispatch = models.BooleanField(
        default=False,
        help_text="Automatically dispatch items predicted with this tag.",
    )
    dispatch_route = models.CharField(
        max_length=120,
        blank=True,
        help_text="Route key used by downstream processing queues.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(auto_dispatch=False)
                | models.Q(dispatch_route__gt=""),
                name="classification_dispatch_route_required_when_auto_dispatch",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a human-readable label."""

        return self.name


class ImageClassifierModel(Entity):
    """Represents a trainable image classifier artifact and selection state."""

    class ModelType(models.TextChoices):
        """Supported classifier families."""

        GENERAL_IMAGE = "general_image", "General Image"

    class Status(models.TextChoices):
        """Lifecycle state for a classifier."""

        DRAFT = "draft", "Draft"
        TRAINING = "training", "Training"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        RETIRED = "retired", "Retired"

    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=120)
    version = models.CharField(max_length=40)
    model_type = models.CharField(
        max_length=30,
        choices=ModelType.choices,
        default=ModelType.GENERAL_IMAGE,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    is_selected = models.BooleanField(
        default=False,
        help_text="When enabled, this model is used for new ingested content.",
    )
    storage_uri = models.CharField(max_length=255, blank=True)
    training_parameters = models.JSONField(default=dict, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    trained_at = models.DateTimeField(null=True, blank=True)
    promoted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_selected", "name", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["model_type"],
                condition=models.Q(is_selected=True, is_deleted=False),
                name="classification_unique_selected_model_per_type",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_selected=False)
                    | models.Q(status="ready")
                    | models.Q(is_deleted=True)
                ),
                name="classification_selected_model_must_be_ready",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a human-readable label."""

        return f"{self.name} ({self.version})"

    @classmethod
    def selected_general_model(cls) -> "ImageClassifierModel | None":
        """Return the currently selected general image classifier, if available."""

        return cls.objects.filter(
            model_type=cls.ModelType.GENERAL_IMAGE,
            status=cls.Status.READY,
            is_selected=True,
        ).first()

    def clean(self) -> None:
        """Validate selection rules for active models."""

        super().clean()
        if self.is_selected and self.status != self.Status.READY:
            raise ValidationError(
                {"status": "A classifier must be ready before it can be selected."},
                code="selected_model_not_ready",
            )

    def save(self, *args, **kwargs):
        """Persist the model and enforce a single selected model."""
        with transaction.atomic():
            if self.is_selected:
                if self.promoted_at is None:
                    self.promoted_at = timezone.now()
                type(self).objects.select_for_update().filter(
                    model_type=self.model_type,
                    is_selected=True,
                    is_deleted=False,
                ).exclude(pk=self.pk).update(is_selected=False)
            self.full_clean()
            super().save(*args, **kwargs)


class TrainingRun(Entity):
    """Tracks each model training execution."""

    class Status(models.TextChoices):
        """Status for training execution."""

        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    classifier = models.ForeignKey(
        ImageClassifierModel,
        on_delete=models.CASCADE,
        related_name="training_runs",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_training_runs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    sample_count = models.PositiveIntegerField(default=0)
    metrics = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a human-readable label."""

        return f"{self.classifier.slug} run {self.pk}"


class TrainingSample(Entity):
    """Image sample and label used in classifier training."""

    class Split(models.TextChoices):
        """Dataset split for training samples."""

        TRAIN = "train", "Train"
        VALIDATION = "validation", "Validation"
        TEST = "test", "Test"

    media_file = models.ForeignKey(
        "media.MediaFile",
        on_delete=models.CASCADE,
        related_name="training_samples",
    )
    tag = models.ForeignKey(
        ClassificationTag,
        on_delete=models.CASCADE,
        related_name="training_samples",
    )
    split = models.CharField(max_length=20, choices=Split.choices, default=Split.TRAIN)
    source = models.CharField(max_length=120, blank=True)
    is_verified = models.BooleanField(default=False)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_training_samples",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["media_file", "tag"],
                name="classification_unique_training_sample_media_tag",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a human-readable label."""

        return f"{self.media_file_id}:{self.tag.slug}"


class ProductModelAssignment(Entity):
    """Maps products to selected classifier models used during dispatch."""

    product = models.ForeignKey(
        "odoo.OdooProduct",
        on_delete=models.CASCADE,
        related_name="classifier_assignments",
    )
    classifier = models.ForeignKey(
        ImageClassifierModel,
        on_delete=models.PROTECT,
        related_name="product_assignments",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Use this classifier by default for this product.",
    )
    min_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal("0.5000"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "classifier"],
                name="classification_unique_product_classifier_assignment",
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_default=True, is_deleted=False),
                name="classification_unique_default_classifier_per_product",
            ),
            models.CheckConstraint(
                condition=models.Q(min_confidence__gte=Decimal("0"))
                & models.Q(min_confidence__lte=Decimal("1")),
                name="classification_product_assignment_min_confidence_between_zero_and_one",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a human-readable label."""

        return f"{self.product} -> {self.classifier}"


class ContentClassification(Entity):
    """Predicted tag output for ingested media content."""

    class Status(models.TextChoices):
        """State of predicted classifications through processing lifecycle."""

        PENDING = "pending", "Pending"
        TAGGED = "tagged", "Tagged"
        DISPATCHED = "dispatched", "Dispatched"
        REJECTED = "rejected", "Rejected"

    media_file = models.ForeignKey(
        "media.MediaFile",
        on_delete=models.CASCADE,
        related_name="classifications",
    )
    classifier = models.ForeignKey(
        ImageClassifierModel,
        on_delete=models.PROTECT,
        related_name="classifications",
    )
    tag = models.ForeignKey(
        ClassificationTag,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="classifications",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1"))],
    )
    route = models.CharField(max_length=120, blank=True)
    is_machine_generated = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    queued_at = models.DateTimeField(default=timezone.now, editable=False)
    classified_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-queued_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(confidence__gte=Decimal("0"))
                & models.Q(confidence__lte=Decimal("1")),
                name="classification_content_confidence_between_zero_and_one",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a human-readable label."""

        if self.tag_id:
            return f"{self.media_file_id} -> {self.tag.slug}"
        return f"{self.media_file_id} -> pending"
