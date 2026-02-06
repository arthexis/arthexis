from __future__ import annotations

import csv

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers import deserialize, serialize
from django.core.serializers.base import DeserializationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.translation import gettext as _, ngettext

from apps.core.entity import Entity

from .fixtures import (
    delete_user_fixture,
    dump_user_fixture,
    fixture_path,
    resolve_fixture_user,
    user_allows_user_data,
)
from .seeds import _seed_datum_is_default, _seed_fixture_index
from .utils import _safe_next_url


class UserDatumAdminMixin(admin.ModelAdmin):
    """Mixin adding a *User Datum* checkbox to change forms."""

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        supports_user_datum = _supports_user_datum(self.model)
        supports_seed_datum = _supports_seed_datum(self.model)
        context["show_user_datum"] = supports_user_datum
        context["show_seed_datum"] = supports_seed_datum
        context["show_save_as_copy"] = (
            issubclass(self.model, Entity)
            or getattr(self.model, "supports_save_as_copy", False)
            or hasattr(self.model, "clone")
        )
        fixture_index = _seed_fixture_index() if supports_seed_datum else None
        if obj is not None:
            context["is_user_datum"] = getattr(obj, "is_user_data", False)
            context["is_seed_datum"] = getattr(obj, "is_seed_data", False)
        else:
            context["is_user_datum"] = False
            context["is_seed_datum"] = False
        context["seed_datum_editable"] = (
            supports_seed_datum
            and (obj is None or not _seed_datum_is_default(obj, index=fixture_index))
        )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if _supports_user_datum(self.model):
            action = self.get_action("toggle_selected_user_data")
            if action is not None:
                actions.setdefault("toggle_selected_user_data", action)
        if _supports_seed_datum(self.model):
            action = self.get_action("toggle_selected_seed_data")
            if action is not None:
                actions.setdefault("toggle_selected_seed_data", action)
        return actions

    @admin.action(description=_("Toggle selected User Data"))
    def toggle_selected_user_data(self, request, queryset):
        if not _supports_user_datum(self.model):
            messages.warning(
                request,
                _("User data is not available for this model."),
            )
            return

        manager = getattr(self.model, "all_objects", self.model._default_manager)
        toggled = 0
        skipped = 0

        for obj in queryset:
            target_user = resolve_fixture_user(obj, request.user)
            allow_user_data = user_allows_user_data(target_user)
            if getattr(obj, "is_user_data", False):
                manager.filter(pk=obj.pk).update(is_user_data=False)
                obj.is_user_data = False
                delete_user_fixture(obj, target_user)
                handler = getattr(self, "user_datum_deleted", None)
                if callable(handler):
                    handler(request, obj)
                toggled += 1
                continue

            if not allow_user_data:
                skipped += 1
                continue

            manager.filter(pk=obj.pk).update(is_user_data=True)
            obj.is_user_data = True
            dump_user_fixture(obj, target_user)
            handler = getattr(self, "user_datum_saved", None)
            if callable(handler):
                handler(request, obj)
            toggled += 1

        if toggled:
            opts = self.model._meta
            self.message_user(
                request,
                ngettext(
                    "Toggled user data for %(count)d %(verbose_name)s.",
                    "Toggled user data for %(count)d %(verbose_name_plural)s.",
                    toggled,
                )
                % {
                    "count": toggled,
                    "verbose_name": opts.verbose_name,
                    "verbose_name_plural": opts.verbose_name_plural,
                },
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d object because user data is not available.",
                    "Skipped %(count)d objects because user data is not available.",
                    skipped,
                )
                % {"count": skipped},
                level=messages.WARNING,
            )

    @admin.action(description=_("Toggle selected Seed Data"))
    def toggle_selected_seed_data(self, request, queryset):
        if not _supports_seed_datum(self.model):
            messages.warning(
                request,
                _("Seed data is not available for this model."),
            )
            return

        manager = getattr(self.model, "all_objects", self.model._default_manager)
        toggled = 0
        skipped = 0
        fixture_index = _seed_fixture_index()

        for obj in queryset:
            if _seed_datum_is_default(obj, index=fixture_index):
                skipped += 1
                continue
            if getattr(obj, "is_seed_data", False):
                manager.filter(pk=obj.pk).update(is_seed_data=False)
                obj.is_seed_data = False
            else:
                manager.filter(pk=obj.pk).update(is_seed_data=True)
                obj.is_seed_data = True
            toggled += 1

        if toggled:
            opts = self.model._meta
            self.message_user(
                request,
                ngettext(
                    "Toggled seed data for %(count)d %(verbose_name)s.",
                    "Toggled seed data for %(count)d %(verbose_name_plural)s.",
                    toggled,
                )
                % {
                    "count": toggled,
                    "verbose_name": opts.verbose_name,
                    "verbose_name_plural": opts.verbose_name_plural,
                },
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d object because it is bundled seed data.",
                    "Skipped %(count)d objects because they are bundled seed data.",
                    skipped,
                )
                % {"count": skipped},
                level=messages.WARNING,
            )


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
            name = (
                opts.verbose_name
                if imported == 1
                else opts.verbose_name_plural
            )
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


class EntityModelAdmin(ImportExportAdminMixin, UserDatumAdminMixin, admin.ModelAdmin):
    """ModelAdmin base class for :class:`Entity` models."""

    change_form_template = "admin/user_datum_change_form.html"
    change_list_template = "admin/base/entity_change_list.html"
    soft_deleted_change_list_template = "admin/base/soft_deleted_change_list.html"
    soft_deleted_purge_template = "admin/base/soft_deleted_purge.html"

    def _supports_soft_delete(self) -> bool:
        return any(field.name == "is_deleted" for field in self.model._meta.fields)

    @admin.display(description="Owner")
    def owner_label(self, obj):
        return obj.owner_display()

    def _admin_view_name(self, suffix: str) -> str:
        opts = self.model._meta
        return f"{opts.app_label}_{opts.model_name}_{suffix}"

    def _soft_deleted_changelist_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('deleted_changelist')}")
        except NoReverseMatch:
            return None

    def _soft_deleted_purge_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('purge_deleted')}")
        except NoReverseMatch:
            return None

    def _active_changelist_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('changelist')}")
        except NoReverseMatch:
            return None

    def get_soft_deleted_queryset(self, request):
        manager = getattr(self.model, "all_objects", self.model._default_manager)
        return manager.filter(is_deleted=True)

    def get_queryset(self, request):
        if getattr(request, "_soft_deleted_only", False):
            return self.get_soft_deleted_queryset(request)
        return super().get_queryset(request)

    def get_urls(self):
        urls = super().get_urls()
        if not self._supports_soft_delete():
            return urls
        custom_urls = [
            path(
                "deleted/",
                self.admin_site.admin_view(self.soft_deleted_changelist_view),
                name=self._admin_view_name("deleted_changelist"),
            ),
            path(
                "deleted/purge/",
                self.admin_site.admin_view(self.purge_deleted_view),
                name=self._admin_view_name("purge_deleted"),
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        if self._supports_soft_delete():
            extra_context.setdefault("soft_delete_supported", True)
            extra_context.setdefault(
                "soft_deleted_url", self._soft_deleted_changelist_url()
            )
            extra_context.setdefault(
                "soft_deleted_purge_url", self._soft_deleted_purge_url()
            )
            extra_context.setdefault(
                "soft_deleted_active_url", self._active_changelist_url()
            )
            extra_context.setdefault(
                "soft_deleted_count",
                self.get_soft_deleted_queryset(request).count(),
            )
        if getattr(request, "_soft_deleted_only", False):
            extra_context.setdefault("soft_deleted_view", True)
        change_list_template = self.change_list_template
        if getattr(request, "_soft_deleted_only", False):
            self.change_list_template = self.soft_deleted_change_list_template
        try:
            return super().changelist_view(request, extra_context=extra_context)
        finally:
            self.change_list_template = change_list_template

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._supports_soft_delete():
            return actions
        if getattr(request, "_soft_deleted_only", False):
            action = self.get_action("recover_selected")
            if action is not None:
                actions.setdefault("recover_selected", action)
        return actions

    @admin.action(description=_("Recover selected"))
    def recover_selected(self, request, queryset):
        if not self._supports_soft_delete():
            messages.warning(
                request,
                _("Recovery is not available for this model."),
            )
            return
        if not self.has_change_permission(request):
            raise PermissionDenied
        manager = getattr(self.model, "all_objects", self.model._default_manager)
        recovered = manager.filter(pk__in=queryset.values_list("pk", flat=True)).update(
            is_deleted=False
        )
        if recovered:
            self.message_user(
                request,
                ngettext(
                    "Recovered %(count)d deleted %(verbose_name)s.",
                    "Recovered %(count)d deleted %(verbose_name_plural)s.",
                    recovered,
                )
                % {
                    "count": recovered,
                    "verbose_name": self.model._meta.verbose_name,
                    "verbose_name_plural": self.model._meta.verbose_name_plural,
                },
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No deleted %(name)s were recovered.")
                % {"name": self.model._meta.verbose_name_plural},
                level=messages.WARNING,
            )

    def soft_deleted_changelist_view(self, request):
        if not self._supports_soft_delete():
            raise Http404
        request._soft_deleted_only = True
        return self.changelist_view(request)

    def _purge_soft_deleted_queryset(self, request):
        queryset = self.get_soft_deleted_queryset(request)
        purged = 0
        protected = []
        for obj in queryset:
            try:
                obj.delete()
            except ProtectedError:
                protected.append(obj)
                continue
            purged += 1
        return purged, protected

    def purge_deleted_view(self, request):
        if not self._supports_soft_delete():
            raise Http404
        if not self.has_delete_permission(request):
            raise PermissionDenied
        soft_deleted_count = self.get_soft_deleted_queryset(request).count()
        if request.method == "POST":
            purged, protected = self._purge_soft_deleted_queryset(request)
            if purged:
                self.message_user(
                    request,
                    ngettext(
                        "Purged %(count)d deleted %(name)s.",
                        "Purged %(count)d deleted %(name)s.",
                        purged,
                    )
                    % {"count": purged, "name": self.model._meta.verbose_name_plural},
                    level=messages.SUCCESS,
                )
            if protected:
                self.message_user(
                    request,
                    ngettext(
                        "Unable to purge %(count)d deleted %(name)s because related objects exist.",
                        "Unable to purge %(count)d deleted %(name)s because related objects exist.",
                        len(protected),
                    )
                    % {
                        "count": len(protected),
                        "name": self.model._meta.verbose_name_plural,
                    },
                    level=messages.ERROR,
                )
            return HttpResponseRedirect(self._soft_deleted_changelist_url() or "..")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Confirm purge of deleted %(name)s")
            % {"name": self.model._meta.verbose_name_plural},
            "soft_deleted_count": soft_deleted_count,
            "soft_deleted_purge_url": self._soft_deleted_purge_url(),
            "soft_deleted_changelist_url": self._soft_deleted_changelist_url(),
            "soft_deleted_active_url": self._active_changelist_url(),
        }
        return TemplateResponse(request, self.soft_deleted_purge_template, context)

    def save_model(self, request, obj, form, change):
        copied = "_saveacopy" in request.POST
        if copied:
            obj = obj.clone() if hasattr(obj, "clone") else obj
            obj.pk = None
            form.instance = obj
            try:
                super().save_model(request, obj, form, False)
            except Exception:
                messages.error(
                    request,
                    _("Unable to save copy. Adjust unique fields and try again."),
                )
                raise
        else:
            super().save_model(request, obj, form, change)
            if isinstance(obj, Entity):
                type(obj).all_objects.filter(pk=obj.pk).update(
                    is_seed_data=obj.is_seed_data, is_user_data=obj.is_user_data
                )
        supports_seed_datum = _supports_seed_datum(self.model)
        if supports_seed_datum:
            fixture_index = _seed_fixture_index()
            if not _seed_datum_is_default(obj, index=fixture_index):
                seed_requested = request.POST.get("_seed_datum") == "on"
                if getattr(obj, "is_seed_data", False) != seed_requested:
                    manager = getattr(type(obj), "all_objects", type(obj)._default_manager)
                    manager.filter(pk=obj.pk).update(is_seed_data=seed_requested)
                    obj.is_seed_data = seed_requested
        if copied:
            return
        if getattr(self, "_skip_entity_user_datum", False):
            return

        target_user = resolve_fixture_user(obj, request.user)
        allow_user_data = user_allows_user_data(target_user)
        if request.POST.get("_user_datum") == "on":
            if allow_user_data:
                if not obj.is_user_data:
                    type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=True)
                    obj.is_user_data = True
                dump_user_fixture(obj, target_user)
                handler = getattr(self, "user_datum_saved", None)
                if callable(handler):
                    handler(request, obj)
                path = fixture_path(target_user, obj)
                self.message_user(request, f"User datum saved to {path}")
            else:
                if obj.is_user_data:
                    type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
                    obj.is_user_data = False
                    delete_user_fixture(obj, target_user)
                messages.warning(
                    request,
                    _("User data is not available for this account."),
                )
        elif obj.is_user_data:
            type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
            obj.is_user_data = False
            delete_user_fixture(obj, target_user)
            handler = getattr(self, "user_datum_deleted", None)
            if callable(handler):
                handler(request, obj)


def patch_admin_user_datum() -> None:
    """Mixin all registered entity admin classes and future registrations."""

    if getattr(admin.site, "_user_datum_patched", False):
        return

    def _patched(admin_class):
        template = (
            getattr(admin_class, "change_form_template", None)
            or EntityModelAdmin.change_form_template
        )
        return type(
            f"Patched{admin_class.__name__}",
            (EntityModelAdmin, admin_class),
            {"change_form_template": template},
        )

    for model, model_admin in list(admin.site._registry.items()):
        if issubclass(model, Entity) and not isinstance(model_admin, EntityModelAdmin):
            admin.site.unregister(model)
            admin.site.register(model, _patched(model_admin.__class__))

    original_register = admin.site.register

    def register(model_or_iterable, admin_class=None, **options):
        models = model_or_iterable
        if not isinstance(models, (list, tuple, set)):
            models = [models]
        admin_class = admin_class or admin.ModelAdmin
        patched_class = admin_class
        for model in models:
            if issubclass(model, Entity) and not issubclass(
                patched_class, EntityModelAdmin
            ):
                patched_class = _patched(patched_class)
        return original_register(model_or_iterable, patched_class, **options)

    admin.site.register = register
    admin.site._user_datum_patched = True


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


def _iter_entity_admin_models():
    """Yield registered :class:`Entity` admin models without proxy duplicates."""

    seen: set[type] = set()
    for model, model_admin in admin.site._registry.items():
        if not issubclass(model, Entity):
            continue
        concrete_model = model._meta.concrete_model
        if concrete_model in seen:
            continue
        seen.add(concrete_model)
        yield model, model_admin




def _supports_user_datum(model) -> bool:
    return issubclass(model, Entity) or getattr(model, "supports_user_datum", False)


def _supports_seed_datum(model) -> bool:
    return issubclass(model, Entity) or getattr(
        model, "supports_seed_datum", _supports_user_datum(model)
    )
