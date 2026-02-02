from __future__ import annotations

import csv

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers import deserialize, serialize
from django.core.serializers.base import DeserializationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _, ngettext

from config.request_utils import is_https_request


def _safe_next_url(request):
    candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return None

    allowed_hosts = {request.get_host()}
    allowed_hosts.update(filter(None, settings.ALLOWED_HOSTS))

    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts=allowed_hosts,
        require_https=is_https_request(request),
    ):
        return candidate
    return None


class ImportExportAdminMixin:
    """Provide import/export actions for all model admins."""

    import_template = "admin/base/model_import.html"
    export_template = "admin/base/model_export.html"

    def _admin_view_name(self, suffix: str) -> str:
        opts = self.model._meta
        return f"{opts.app_label}_{opts.model_name}_{suffix}"

    def _export_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('export')}")
        except NoReverseMatch:
            return None

    def _import_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('import')}")
        except NoReverseMatch:
            return None

    @staticmethod
    def _has_route(urls, route: str) -> bool:
        return any(getattr(url.pattern, "_route", None) == route for url in urls)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = []
        if not self._has_route(urls, "export/"):
            custom_urls.append(
                path(
                    "export/",
                    self.admin_site.admin_view(self.export_view),
                    name=self._admin_view_name("export"),
                )
            )
        if not self._has_route(urls, "import/"):
            custom_urls.append(
                path(
                    "import/",
                    self.admin_site.admin_view(self.import_view),
                    name=self._admin_view_name("import"),
                )
            )
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        export_querystring = request.GET.copy()
        export_querystring.pop("format", None)
        if self.has_view_permission(request):
            extra_context.setdefault("model_export_url", self._export_url())
            extra_context.setdefault("export_querystring", export_querystring.urlencode())
        if self.has_add_permission(request) or self.has_change_permission(request):
            extra_context.setdefault("model_import_url", self._import_url())
        return super().changelist_view(request, extra_context=extra_context)

    def _get_export_fields(self, request):
        opts = self.model._meta
        field_names = list(self.get_fields(request))
        if not field_names:
            return list(opts.fields)
        field_map = {field.name: field for field in opts.fields}
        export_fields = []
        for name in field_names:
            field = field_map.get(name)
            if field and field not in export_fields:
                export_fields.append(field)
        return export_fields or list(opts.fields)

    @staticmethod
    def _sanitize_csv_value(value):
        if value is None:
            return ""
        text = str(value)
        if text.startswith(("=", "+", "-", "@")):
            return f"'{text}"
        return text

    def export_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        params = request.POST if request.method == "POST" else request.GET
        export_format = params.get("format", "").lower()
        original_get = request.GET
        filtered_get = request.GET.copy()
        filtered_get.pop("format", None)
        request.GET = filtered_get
        try:
            changelist = self.get_changelist_instance(request)
            queryset = changelist.get_queryset(request)
        finally:
            request.GET = original_get
        opts = self.model._meta
        export_fields = self._get_export_fields(request)
        export_field_names = [field.name for field in export_fields]
        if export_format:
            if export_format == "csv":
                response = HttpResponse(content_type="text/csv")
                response["Content-Disposition"] = (
                    f"attachment; filename={opts.app_label}_{opts.model_name}.csv"
                )
                writer = csv.writer(response)
                writer.writerow(export_field_names)
                for obj in queryset:
                    writer.writerow(
                        [
                            self._sanitize_csv_value(field.value_from_object(obj))
                            for field in export_fields
                        ]
                    )
                return response
            if export_format == "json":
                payload = serialize("json", queryset, fields=export_field_names)
                response = HttpResponse(payload, content_type="application/json")
                response["Content-Disposition"] = (
                    f"attachment; filename={opts.app_label}_{opts.model_name}.json"
                )
                return response
            return HttpResponseBadRequest(_("Unsupported export format."))
        changelist_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_changelist"
        )
        context = admin.site.each_context(request)
        context.update(
            {
                "title": _("Export %(name)s") % {"name": opts.verbose_name_plural},
                "opts": opts,
                "changelist_url": changelist_url,
                "export_count": queryset.count(),
                "export_columns": [
                    {"name": field.name, "label": field.verbose_name}
                    for field in export_fields
                ],
                "export_formats": [
                    {"value": "json", "label": _("JSON")},
                    {"value": "csv", "label": _("CSV")},
                ],
            }
        )
        return TemplateResponse(request, self.export_template, context)

    def import_view(self, request):
        can_add = self.has_add_permission(request)
        can_change = self.has_change_permission(request)
        if not (can_add or can_change):
            raise PermissionDenied
        opts = self.model._meta
        changelist_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_changelist"
        )
        if request.method == "POST" and request.FILES.get("import_file"):
            imported = 0
            import_file = request.FILES["import_file"]
            try:
                with transaction.atomic():
                    for deserialized_object in deserialize("json", import_file):
                        if (
                            deserialized_object.object._meta.concrete_model
                            is not self.model._meta.concrete_model
                        ):
                            raise ValidationError(
                                _(
                                    "Imported data contains objects of an unexpected model type."
                                )
                            )
                        if not can_add:
                            pk = deserialized_object.object.pk
                            if pk is None or not self.model._default_manager.filter(
                                pk=pk
                            ).exists():
                                raise ValidationError(
                                    _(
                                        "You do not have permission to add new %(name)s records."
                                    )
                                    % {"name": opts.verbose_name_plural}
                                )
                        deserialized_object.save()
                        imported += 1
            except (DeserializationError, IntegrityError, ValidationError, ValueError) as exc:
                self.message_user(
                    request,
                    _("Error processing import: %(error)s") % {"error": exc},
                    level=messages.ERROR,
                )
                return HttpResponseRedirect(_safe_next_url(request) or changelist_url)
            name = opts.verbose_name if imported == 1 else opts.verbose_name_plural
            self.message_user(
                request,
                ngettext(
                    "Imported %(count)d %(name)s.",
                    "Imported %(count)d %(name)s.",
                    imported,
                )
                % {"count": imported, "name": name},
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(_safe_next_url(request) or changelist_url)
        context = admin.site.each_context(request)
        context.update(
            {
                "title": _("Import %(name)s") % {"name": opts.verbose_name_plural},
                "opts": opts,
                "changelist_url": changelist_url,
            }
        )
        return TemplateResponse(request, self.import_template, context)


def patch_admin_import_export() -> None:
    """Mixin import/export actions into registered admin classes."""

    if getattr(admin.site, "_import_export_patched", False):
        return

    def _patched(admin_class):
        return type(
            f"ImportExport{admin_class.__name__}",
            (ImportExportAdminMixin, admin_class),
            {},
        )

    for model, model_admin in list(admin.site._registry.items()):
        if not isinstance(model_admin, ImportExportAdminMixin):
            admin.site.unregister(model)
            admin.site.register(model, _patched(model_admin.__class__))

    original_register = admin.site.register

    def register(model_or_iterable, admin_class=None, **options):
        models = model_or_iterable
        if not isinstance(models, (list, tuple, set)):
            models = [models]
        admin_class = admin_class or admin.ModelAdmin
        patched_class = admin_class
        if not issubclass(patched_class, ImportExportAdminMixin):
            patched_class = _patched(patched_class)
        return original_register(model_or_iterable, patched_class, **options)

    admin.site.register = register
    admin.site._import_export_patched = True
