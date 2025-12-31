from __future__ import annotations

from django.contrib import admin

from apps.core.admin import EntityModelAdmin

from ..models import Feature, FeatureArtifact, FeatureTestCase


class FeatureArtifactInline(admin.TabularInline):
    model = FeatureArtifact
    extra = 1
    fields = ("label", "attachment", "content")


class FeatureTestCaseInline(admin.TabularInline):
    model = FeatureTestCase
    extra = 0
    readonly_fields = ("test_node_id", "test_name", "last_status", "last_duration")
    fields = readonly_fields + ("last_log",)


@admin.register(Feature)
class FeatureAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "package",
        "expected_version",
        "is_active",
    )
    list_filter = ("package", "is_active")
    search_fields = ("name", "slug", "package__name", "summary")
    inlines = [FeatureArtifactInline, FeatureTestCaseInline]
    fieldsets = (
        (None, {"fields": ("package", "name", "slug", "summary", "is_active")}),
        (
            "Expectations",
            {
                "fields": (
                    "expected_version",
                    "scope",
                    "content",
                )
            },
        ),
    )


@admin.register(FeatureArtifact)
class FeatureArtifactAdmin(EntityModelAdmin):
    list_display = ("label", "feature")
    search_fields = ("label", "feature__name")


@admin.register(FeatureTestCase)
class FeatureTestCaseAdmin(EntityModelAdmin):
    list_display = ("test_name", "feature", "last_status", "last_duration")
    search_fields = ("test_name", "test_node_id", "feature__name")
    readonly_fields = ("test_node_id", "last_status", "last_duration")
    fields = ("feature", "test_node_id", "test_name", "last_status", "last_duration", "last_log")
