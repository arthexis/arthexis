"""Admin registrations for classifier orchestration models."""

from django.contrib import admin

from .models import (
    ClassificationTag,
    ContentClassification,
    ImageClassifierModel,
    ProductModelAssignment,
    TrainingRun,
    TrainingSample,
)


@admin.register(ClassificationTag)
class ClassificationTagAdmin(admin.ModelAdmin):
    """Admin for label taxonomy tags."""

    list_display = ("name", "slug", "auto_dispatch", "dispatch_route", "is_active")
    list_filter = ("auto_dispatch", "is_active")
    search_fields = ("name", "slug", "description", "dispatch_route")


@admin.register(ImageClassifierModel)
class ImageClassifierModelAdmin(admin.ModelAdmin):
    """Admin for model artifacts and selection state."""

    list_display = ("name", "version", "model_type", "status", "is_selected", "updated_at")
    list_filter = ("model_type", "status", "is_selected")
    search_fields = ("name", "slug", "version", "storage_uri")
    readonly_fields = ("promoted_at", "created_at", "updated_at")


@admin.register(TrainingRun)
class TrainingRunAdmin(admin.ModelAdmin):
    """Admin for model training execution history."""

    list_display = ("classifier", "status", "sample_count", "started_at", "finished_at")
    list_filter = ("status", "classifier")
    search_fields = ("classifier__slug", "notes")


@admin.register(TrainingSample)
class TrainingSampleAdmin(admin.ModelAdmin):
    """Admin for dataset samples and labels."""

    list_display = ("media_file", "tag", "split", "is_verified", "created_at")
    list_filter = ("split", "is_verified", "tag")
    search_fields = ("media_file__original_name", "tag__slug", "source")


@admin.register(ProductModelAssignment)
class ProductModelAssignmentAdmin(admin.ModelAdmin):
    """Admin for product-to-classifier routing preferences."""

    list_display = ("product", "classifier", "is_default", "min_confidence", "updated_at")
    list_filter = ("is_default", "classifier")
    search_fields = ("product__name", "classifier__slug")


@admin.register(ContentClassification)
class ContentClassificationAdmin(admin.ModelAdmin):
    """Admin for classification outcomes and dispatch records."""

    list_display = (
        "media_file",
        "classifier",
        "tag",
        "status",
        "confidence",
        "route",
        "classified_at",
        "dispatched_at",
    )
    list_filter = ("status", "classifier", "tag")
    search_fields = ("media_file__original_name", "tag__slug", "route")
    readonly_fields = ("classified_at", "dispatched_at")
