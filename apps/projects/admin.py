"""Admin registration for project bundles."""

from __future__ import annotations

import json
import zipfile

from django.contrib import admin, messages
from django.contrib.auth import get_permission_codename
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.locals.entity import EntityModelAdmin
from apps.projects.models import Project, ProjectItem
from apps.projects.services import build_project_bundle_response, import_project_bundle


BUNDLE_VIEW_NAME = "admin:projects_project_bundle"


class ProjectItemInline(admin.TabularInline):
    """Inline for linked bundle items."""

    model = ProjectItem
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Project)
class ProjectAdmin(EntityModelAdmin):
    """Admin for bundle project headers."""

    list_display = ("name", "item_count", "is_seed_data", "is_user_data")
    search_fields = ("name", "description")
    inlines = (ProjectItemInline,)
    change_form_template = "admin/projects/project/change_form.html"

    def get_urls(self):
        """Add project bundle URLs to the default admin routes."""

        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/bundle/",
                self.admin_site.admin_view(self.bundle_view),
                name="projects_project_bundle",
            ),
            path(
                "<path:object_id>/bundle/export.zip",
                self.admin_site.admin_view(self.bundle_export_view),
                name="projects_project_bundle_export",
            ),
            path(
                "<path:object_id>/bundle/import/",
                self.admin_site.admin_view(self.bundle_import_view),
                name="projects_project_bundle_import",
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Items")
    def item_count(self, obj: Project) -> int:
        """Return the number of linked bundle items."""

        return obj.items.count()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Expose link to bundle view from project change form."""

        extra_context = extra_context or {}
        extra_context["bundle_url"] = reverse(BUNDLE_VIEW_NAME, args=[object_id])
        return super().change_view(
            request, object_id, form_url=form_url, extra_context=extra_context
        )

    def _get_project_for_bundle(self, request: HttpRequest, object_id: str) -> Project:
        """Load project and enforce object-level view/change permissions."""

        project = get_object_or_404(Project, pk=object_id)
        if not self.has_view_or_change_permission(request, obj=project):
            raise PermissionDenied
        return project

    def bundle_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Render and update bundle membership for linked instances."""

        project = self._get_project_for_bundle(request, object_id)
        if request.method == "POST" and request.POST.get("_bundle_selection") == "1":
            if not self.has_change_permission(request, obj=project):
                raise PermissionDenied
            selected_item_ids = set(request.POST.getlist("selected_items"))
            removable_items = project.items.exclude(pk__in=selected_item_ids)
            removed_count = removable_items.count()
            if removed_count:
                removable_items.delete()
                self.message_user(
                    request,
                    _("Removed %(count)d bundle item(s).") % {"count": removed_count},
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    _("No bundle items were removed."),
                    level=messages.INFO,
                )
            return redirect(BUNDLE_VIEW_NAME, object_id)

        items = project.items.select_related("content_type")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "project": project,
            "items": items,
            "title": _("Project bundle: %(name)s") % {"name": project.name},
            "bundle_url": reverse(BUNDLE_VIEW_NAME, args=[project.pk]),
            "change_url": reverse("admin:projects_project_change", args=[project.pk]),
            "export_url": reverse("admin:projects_project_bundle_export", args=[project.pk]),
            "import_url": reverse("admin:projects_project_bundle_import", args=[project.pk]),
        }
        return TemplateResponse(request, "admin/projects/project/bundle.html", context)

    def bundle_export_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Return a ZIP response for the selected project bundle."""

        project = self._get_project_for_bundle(request, object_id)
        return build_project_bundle_response(project)

    def bundle_import_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Import a ZIP bundle into the selected project and redirect."""

        project = self._get_project_for_bundle(request, object_id)
        if request.method != "POST":
            return redirect(BUNDLE_VIEW_NAME, object_id)
        if not self.has_change_permission(request, obj=project):
            raise PermissionDenied
        bundle_file = request.FILES.get("bundle_file")
        if bundle_file is None:
            self.message_user(request, _("Select a ZIP file to import."), level=messages.ERROR)
            return redirect(BUNDLE_VIEW_NAME, object_id)
        allowed_models = {
            f"{model._meta.app_label}.{model._meta.model_name}"
            for model in ProjectItem.get_bundle_model_classes()
            if request.user.has_perm(
                f"{model._meta.app_label}.{get_permission_codename('add', model._meta)}"
            )
        }

        try:
            imported_objects, linked = import_project_bundle(
                project,
                bundle_file,
                allowed_models=allowed_models,
            )
        except (ValidationError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
            self.message_user(
                request,
                _("Unable to import project bundle. Verify the ZIP structure and data."),
                level=messages.ERROR,
            )
            return redirect(BUNDLE_VIEW_NAME, object_id)
        self.message_user(
            request,
            _("Imported %(objects)d objects and linked %(links)d items.")
            % {"objects": imported_objects, "links": linked},
            level=messages.SUCCESS,
        )
        return redirect(BUNDLE_VIEW_NAME, object_id)


@admin.register(ProjectItem)
class ProjectItemAdmin(admin.ModelAdmin):
    """Admin for direct editing of project links."""

    list_display = (
        "project",
        "content_type",
        "object_id",
        "content_object_label",
        "created_at",
    )
    list_filter = ("project", "content_type")
    search_fields = ("object_id", "project__name", "note")
    autocomplete_fields = ("project",)

    @admin.display(description="Object")
    def content_object_label(self, obj: ProjectItem) -> str:
        """Return string label for the linked object."""

        if obj.content_object is None:
            return "(missing)"
        return str(obj.content_object)
