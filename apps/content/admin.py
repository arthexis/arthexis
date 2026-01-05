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
    WebRequestSampler,
    WebRequestStep,
    WebSample,
    WebSampleAttachment,
)
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node
from apps.nodes.utils import capture_screenshot, save_screenshot
from .web_sampling import execute_sampler


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


class WebRequestStepInline(admin.TabularInline):
    model = WebRequestStep
    extra = 0
    fields = (
        "order",
        "slug",
        "name",
        "curl_command",
        "save_as_content",
        "attachment_kind",
    )


@admin.register(WebRequestSampler)
class WebRequestSamplerAdmin(EntityModelAdmin):
    list_display = (
        "label",
        "slug",
        "sampling_period_minutes",
        "last_sampled_at",
        "owner",
        "security_group",
    )
    search_fields = ("label", "slug", "description")
    list_filter = ("sampling_period_minutes", "owner", "security_group")
    actions = ("execute_selected_samplers",)
    inlines = (WebRequestStepInline,)

    @admin.action(description="Execute selected Samplers")
    def execute_selected_samplers(self, request, queryset):
        executed = 0
        for sampler in queryset:
            try:
                execute_sampler(sampler, user=request.user)
                executed += 1
            except Exception as exc:  # pragma: no cover - admin message only
                self.message_user(request, str(exc), level=messages.ERROR)
        if executed:
            self.message_user(
                request,
                f"Executed {executed} sampler(s)",
                level=messages.SUCCESS,
            )


class WebSampleAttachmentInline(admin.TabularInline):
    model = WebSampleAttachment
    extra = 0
    readonly_fields = ("content_sample", "uri", "step")


@admin.register(WebSample)
class WebSampleAdmin(EntityModelAdmin):
    list_display = ("sampler", "executed_by", "created_at")
    readonly_fields = ("document", "executed_by", "created_at")
    inlines = (WebSampleAttachmentInline,)
