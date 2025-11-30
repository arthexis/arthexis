import base64
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path
from django.utils.html import format_html

from apps.content.models import (
    ContentClassification,
    ContentClassifier,
    ContentSample,
    ContentTag,
)
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node
from apps.nodes.utils import capture_screenshot, save_screenshot


@admin.register(ContentTag)
class ContentTagAdmin(EntityModelAdmin):
    list_display = ("label", "slug")
    search_fields = ("label", "slug")


@admin.register(ContentClassifier)
class ContentClassifierAdmin(EntityModelAdmin):
    list_display = ("label", "slug", "kind", "run_by_default", "active")
    list_filter = ("kind", "run_by_default", "active")
    search_fields = ("label", "slug", "entrypoint")


class ContentClassificationInline(admin.TabularInline):
    model = ContentClassification
    extra = 0
    autocomplete_fields = ("classifier", "tag")


@admin.register(ContentSample)
class ContentSampleAdmin(EntityModelAdmin):
    list_display = ("name", "kind", "node", "user", "created_at")
    readonly_fields = ("created_at", "name", "user", "image_preview")
    inlines = (ContentClassificationInline,)
    list_filter = ("kind", "classifications__tag")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "capture/",
                self.admin_site.admin_view(self.capture_now),
                name="nodes_contentsample_capture",
            ),
        ]
        return custom + urls

    def capture_now(self, request):
        node = Node.get_local()
        url = request.build_absolute_uri("/")
        try:
            path = capture_screenshot(url)
        except Exception as exc:  # pragma: no cover - depends on selenium setup
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        sample = save_screenshot(path, node=node, method="ADMIN")
        if sample:
            self.message_user(request, f"Screenshot saved to {path}", messages.SUCCESS)
        else:
            self.message_user(request, "Duplicate screenshot; not saved", messages.INFO)
        return redirect("..")

    @admin.display(description="Screenshot")
    def image_preview(self, obj):
        if not obj or obj.kind != ContentSample.IMAGE or not obj.path:
            return ""
        file_path = Path(obj.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        if not file_path.exists():
            return "File not found"
        with file_path.open("rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return format_html(
            '<img src="data:image/png;base64,{}" style="max-width:100%;" />',
            encoded,
        )
