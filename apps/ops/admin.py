"""Admin registrations for operations models."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from .models import OperationExecution, OperationScreen


@admin.register(OperationScreen)
class OperationScreenAdmin(admin.ModelAdmin):
    """Admin interface for operation screens."""

    list_display = (
        "title",
        "priority",
        "scope",
        "is_required",
        "is_active",
        "owner",
        "start_button",
    )
    list_filter = ("is_required", "is_active", "scope")
    search_fields = ("title", "slug", "description")
    prepopulated_fields = {"slug": ("title",)}

    def get_urls(self):
        """Expose custom admin start endpoint for operation sessions."""

        return [
            path(
                "<int:operation_id>/start/",
                self.admin_site.admin_view(self.start_view),
                name="ops_operationscreen_start",
            )
        ] + super().get_urls()

    @admin.display(description="Start")
    def start_button(self, obj):
        """Render a quick-start button from changelist."""

        url = reverse("admin:ops_operationscreen_start", args=[obj.pk])
        return format_html('<a class="button" href="{}">Start</a>', url)

    def start_view(self, request, operation_id: int):
        """Store active operation in session and redirect to start URL."""

        operation = get_object_or_404(OperationScreen, pk=operation_id)
        request.session["ops_active_operation_id"] = operation.pk
        separator = "&" if "?" in operation.start_url else "?"
        return HttpResponseRedirect(f"{operation.start_url}{separator}ops={operation.pk}")


@admin.register(OperationExecution)
class OperationExecutionAdmin(admin.ModelAdmin):
    """Admin interface for operation completion logs."""

    list_display = ("operation", "user", "node", "completed_at", "mark_complete_action")
    list_filter = ("operation", "completed_at")
    search_fields = ("operation__title", "user__username", "notes")
    autocomplete_fields = ("operation", "user", "node")

    @admin.display(description="Start")
    def mark_complete_action(self, obj):
        """Provide a jump-back button to operation start URL."""

        start_url = f"{obj.operation.start_url}{'&' if '?' in obj.operation.start_url else '?'}ops={obj.operation_id}"
        return format_html('<a class="button" href="{}">Start</a>', start_url)
