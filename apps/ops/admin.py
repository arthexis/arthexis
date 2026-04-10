"""Admin configuration for operation screen management."""

from django import forms
from django.contrib import admin
from django.http import Http404
from django.urls import path, reverse
from django.utils.html import format_html

from .models import (
    OperationExecution,
    OperationLink,
    OperationScreen,
    OperatorJourney,
    OperatorJourneyStep,
    OperatorJourneyStepCompletion,
    SecurityAlertEvent,
    validate_local_absolute_path_url,
)
from .redirects import safe_host_redirect


class OperationScreenAdminForm(forms.ModelForm):
    """Admin form that enforces start URL redirect safety constraints."""

    class Meta:
        model = OperationScreen
        fields = "__all__"

    def clean_start_url(self) -> str:
        """Return validated start URL value."""

        start_url = self.cleaned_data.get("start_url", "")
        validate_local_absolute_path_url(start_url)
        return start_url


class OperationLinkInline(admin.TabularInline):
    """Manage external reference links inline on operation records."""

    model = OperationLink
    extra = 1


@admin.register(OperationScreen)
class OperationScreenAdmin(admin.ModelAdmin):
    """Admin for operation definitions with a Start flow action."""

    list_display = (
        "title",
        "priority",
        "scope",
        "is_required",
        "is_active",
        "owner",
        "start_button",
    )
    list_filter = ("scope", "is_required", "is_active")
    search_fields = ("title", "slug", "description", "start_url")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [OperationLinkInline]
    form = OperationScreenAdminForm

    def get_urls(self):
        """Expose custom admin start endpoint for operations."""

        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:operation_id>/start/",
                self.admin_site.admin_view(self.start_operation_view),
                name="ops_operationscreen_start",
            )
        ]
        return custom_urls + urls

    @admin.display(description="Start")
    def start_button(self, obj: OperationScreen) -> str:
        """Render small start button linking to launch endpoint."""

        url = reverse("admin:ops_operationscreen_start", args=[obj.pk])
        return format_html('<a class="button" href="{}">Start</a>', url)

    def start_operation_view(self, request, operation_id: int):
        """Persist active operation in session and redirect to start URL."""

        operation = OperationScreen.objects.filter(pk=operation_id).first()
        if operation is None:
            raise Http404("Operation not found")
        request.session["ops_active_operation_id"] = operation.id
        return safe_host_redirect(request, operation.start_url)


@admin.register(OperationExecution)
class OperationExecutionAdmin(admin.ModelAdmin):
    """Admin for operation execution logs."""

    list_display = ("operation", "user", "node", "performed_at", "validation_passed")
    list_filter = ("operation", "validation_passed", "performed_at")
    search_fields = ("operation__title", "user__username", "notes")


@admin.register(OperationLink)
class OperationLinkAdmin(admin.ModelAdmin):
    """Admin for operation supplemental links."""

    list_display = ("label", "operation", "priority", "url")
    search_fields = ("label", "url", "operation__title")


@admin.register(SecurityAlertEvent)
class SecurityAlertEventAdmin(admin.ModelAdmin):
    """Admin for aggregated security alert event records."""

    list_display = ("key", "severity", "message", "occurrence_count", "last_occurred_at", "is_active")
    list_filter = ("severity", "is_active", "last_occurred_at")
    search_fields = ("key", "message", "detail")
    readonly_fields = ("occurrence_count", "last_occurred_at", "created_at", "updated_at")


class OperatorJourneyStepInline(admin.TabularInline):
    """Manage linear journey steps inline."""

    model = OperatorJourneyStep
    extra = 1


@admin.register(OperatorJourney)
class OperatorJourneyAdmin(admin.ModelAdmin):
    """Admin for operator journey workflow definitions."""

    list_display = ("name", "security_group", "priority", "is_active")
    list_filter = ("is_active", "security_group")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [OperatorJourneyStepInline]


@admin.register(OperatorJourneyStep)
class OperatorJourneyStepAdmin(admin.ModelAdmin):
    """Admin for individual operator journey steps."""

    list_display = ("title", "journey", "order", "is_active")
    list_filter = ("is_active", "journey")
    search_fields = ("title", "slug", "instruction", "help_text")
    ordering = ("journey__priority", "journey__name", "order")


@admin.register(OperatorJourneyStepCompletion)
class OperatorJourneyStepCompletionAdmin(admin.ModelAdmin):
    """Admin for per-user journey progress markers."""

    list_display = ("user", "step", "completed_at")
    list_filter = ("step__journey", "completed_at")
    search_fields = ("user__username", "step__title", "step__journey__name")
