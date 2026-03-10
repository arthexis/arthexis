from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.utils import flatten_fieldsets
from django.core.exceptions import FieldError, PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _, ngettext
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin
from apps.locals.entity import EntityModelAdmin
from apps.services.celery_workers import (
    CELERY_WORKERS_FEATURE_SLUG,
    sync_celery_workers_from_feature,
)

from .models import Feature, FeatureNote, FeatureTest
from .parameters import get_feature_parameter_definitions, set_feature_parameter_values


def _autogrow_textarea_widget() -> forms.Textarea:
    """Return a compact textarea widget that grows automatically in the browser."""

    return forms.Textarea(attrs={"rows": 1, "class": "feature-admin-autogrow"})


class FeatureTestInline(admin.TabularInline):
    model = FeatureTest
    extra = 0
    fields = ("name", "node_id", "is_regression_guard", "notes")
    formfield_overrides = {
        models.TextField: {"widget": _autogrow_textarea_widget()},
    }


class FeatureNoteInline(admin.TabularInline):
    model = FeatureNote
    extra = 0
    fields = ("author", "body", "updated_at")
    readonly_fields = ("updated_at",)
    formfield_overrides = {
        models.TextField: {"widget": _autogrow_textarea_widget()},
    }


class SourceAppListFilter(admin.SimpleListFilter):
    """Filter suite features by source app values that are currently in use."""

    title = _("From app")
    parameter_name = "main_app"

    def lookups(self, request, model_admin):
        """Return app choices that are referenced by at least one suite feature."""

        apps = (
            Application.objects.filter(
                features__in=model_admin.get_queryset(request).exclude(main_app__isnull=True)
            )
            .distinct()
            .order_by("name")
        )
        return [(str(app.pk), app.display_name) for app in apps]

    def queryset(self, request, queryset):
        """Constrain changelist rows to the selected source app id."""

        del request
        value = self.value()
        if not value:
            return queryset
        try:
            app_id = int(value)
        except ValueError:
            return queryset.none()
        return queryset.filter(main_app_id=app_id)


class FeatureAdminForm(forms.ModelForm):
    """Feature admin form with dynamic parameter fields."""

    PARAM_FIELD_PREFIX = "param__"

    class Meta:
        model = Feature
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._parameter_keys: list[str] = []
        slug = (self.instance.slug or "").strip()
        known_dynamic_field_names = {
            name for name in self.fields if name.startswith(self.PARAM_FIELD_PREFIX)
        }
        metadata = self.instance.metadata if isinstance(self.instance.metadata, dict) else {}
        parameters = metadata.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}

        for definition in get_feature_parameter_definitions(slug):
            field_name = f"{self.PARAM_FIELD_PREFIX}{definition.key}"
            self._parameter_keys.append(definition.key)
            if definition.choices:
                field = forms.ChoiceField(required=False, choices=definition.choices)
            else:
                field = forms.CharField(required=False)
            field.label = definition.label
            field.help_text = definition.help_text
            field.initial = definition.default
            self.fields[field_name] = field
            initial_value = parameters.get(definition.key, definition.default)
            if not initial_value:
                initial_value = definition.default
            self.initial[field_name] = str(initial_value).strip()

        for field_name in known_dynamic_field_names:
            key = field_name.replace(self.PARAM_FIELD_PREFIX, "", 1)
            if key not in self._parameter_keys:
                self.fields.pop(field_name, None)

    def clean(self):
        """Validate dynamic parameter values using feature parameter definitions."""

        cleaned_data = super().clean()
        slug = (cleaned_data.get("slug") or self.instance.slug or "").strip()
        if not slug:
            return cleaned_data

        definitions = {d.key: d for d in get_feature_parameter_definitions(slug)}
        for key, definition in definitions.items():
            field_name = f"{self.PARAM_FIELD_PREFIX}{key}"
            if field_name not in self.fields:
                continue
            try:
                cleaned_data[field_name] = definition.normalize(cleaned_data.get(field_name))
            except ValueError as exc:
                self.add_error(field_name, str(exc))

        return cleaned_data

    def cleaned_parameter_values(self) -> dict[str, str]:
        """Return normalized dynamic parameter values ready for persistence."""

        values: dict[str, str] = {}
        for key in self._parameter_keys:
            field_name = f"{self.PARAM_FIELD_PREFIX}{key}"
            if field_name in self.cleaned_data:
                values[key] = self.cleaned_data[field_name]
        return values


@admin.register(Feature)
class FeatureAdmin(DjangoObjectActions, OwnableAdminMixin, EntityModelAdmin):
    form = FeatureAdminForm
    change_list_template = "django_object_actions/change_list.html"
    changelist_actions = ("reload_base",)
    actions = ("toggle_selected_feature",)

    list_display = (
        "display",
        "slug",
        "source",
        "is_enabled",
        "main_app",
        "node_feature",
    )
    list_filter = ("source", "is_enabled", SourceAppListFilter)
    search_fields = ("display", "slug", "summary")
    readonly_fields = ("source",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "display",
                    "slug",
                    "source",
                    "summary",
                    "is_enabled",
                    "main_app",
                    "node_feature",
                )
            },
        ),
        (
            _("Feature surfaces"),
            {
                "fields": (
                    "admin_requirements",
                    "public_requirements",
                    "service_requirements",
                    "admin_views",
                    "public_views",
                    "service_views",
                    "metadata",
                )
            },
        ),
        (
            _("Coverage"),
            {
                "fields": (
                    "code_locations",
                    "protocol_coverage",
                )
            },
        ),
    )
    inlines = [FeatureNoteInline, FeatureTestInline]
    formfield_overrides = {
        models.TextField: {"widget": _autogrow_textarea_widget()},
        models.JSONField: {"widget": _autogrow_textarea_widget()},
    }

    class Media:
        js = ("features/admin/feature_admin_autogrow.js",)

    def _mainstream_fixture_paths(self) -> list[Path]:
        """Return fixture files used to seed mainstream suite features."""

        fixtures_dir = Path(settings.BASE_DIR) / "apps" / "features" / "fixtures"
        return sorted(fixtures_dir.glob("features__*.json"))

    def _reload_all_preview_context(self) -> dict[str, object]:
        """Build the preview context for the reload-all confirmation view."""

        fixture_paths = self._mainstream_fixture_paths()
        feature_manager = getattr(self.model, "all_objects", self.model._default_manager)
        active_feature_count = feature_manager.filter(is_deleted=False).count()
        fixture_names = [path.name for path in fixture_paths]
        return {
            "fixture_paths": fixture_paths,
            "fixture_names": fixture_names,
            "active_feature_count": active_feature_count,
            "fixture_count": len(fixture_paths),
            "opts": self.model._meta,
            "title": _("Confirm Reload All"),
        }

    def reload_base(self, request, queryset=None):
        """Preview and optionally reload all suite features from mainstream fixtures."""

        del queryset

        if not self.has_delete_permission(request):
            raise PermissionDenied

        preview_context = self._reload_all_preview_context()

        if request.method != "POST" or request.POST.get("confirm") != "yes":
            return TemplateResponse(
                request,
                "admin/features/feature/reload_all_confirmation.html",
                preview_context,
            )

        fixture_paths = preview_context["fixture_paths"]
        if not fixture_paths:
            self.message_user(request, _("No feature fixtures found."), level=messages.WARNING)
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

        deleted_count = preview_context["active_feature_count"]
        try:
            feature_manager = getattr(self.model, "all_objects", self.model._default_manager)
            with transaction.atomic():
                feature_manager.update(is_seed_data=False, is_enabled=False)
                feature_manager.all().delete()
                call_command("loaddata", *(str(path) for path in fixture_paths), verbosity=0)
        except CommandError as exc:
            self.message_user(
                request,
                _("Failed to reload fixtures: %(error)s") % {"error": exc},
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

        self.message_user(
            request,
            ngettext(
                "Dropped %(count)d suite feature before full reload.",
                "Dropped %(count)d suite features before full reload.",
                deleted_count,
            )
            % {"count": deleted_count},
            level=messages.SUCCESS,
        )
        self.message_user(
            request,
            ngettext(
                "Reloaded %(count)d mainstream fixture.",
                "Reloaded %(count)d mainstream fixtures.",
                len(fixture_paths),
            )
            % {"count": len(fixture_paths)},
            level=messages.SUCCESS,
        )

        return HttpResponseRedirect(reverse("admin:features_feature_changelist"))

    reload_base.label = _("Reload All")
    reload_base.short_description = _("Reload All")
    reload_base.requires_queryset = False
    reload_base.methods = ("GET", "POST")


    def response_action(self, request, queryset):
        """Handle denied bulk actions with explicit admin feedback."""

        selected_action = request.POST.get("action")
        if (
            selected_action == "toggle_selected_feature"
            and not self.has_change_permission(request)
        ):
            self.message_user(
                request,
                _("You do not have permission to run this action."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(request.get_full_path())

        return super().response_action(request, queryset)

    def changelist_view(self, request, extra_context=None):
        """Emit feedback when a posted bulk action is not permitted."""

        selected_action = request.POST.get("action") if request.method == "POST" else None
        if (
            selected_action == "toggle_selected_feature"
            and not self.has_change_permission(request)
        ):
            self.message_user(
                request,
                _("You do not have permission to run this action."),
                level=messages.WARNING,
            )

        return super().changelist_view(request, extra_context=extra_context)

    @admin.action(description=_("Toggle selected feature"), permissions=["change"])
    def toggle_selected_feature(self, request, queryset):
        """Flip the enabled state for each selected suite feature."""

        toggled_total = 0
        enabled_total = 0
        disabled_total = 0

        with transaction.atomic():
            for feature in queryset.select_for_update().only("pk", "is_enabled"):
                changed = feature.set_enabled(not feature.is_enabled)
                if not changed:
                    continue
                toggled_total += 1
                if feature.is_enabled:
                    enabled_total += 1
                else:
                    disabled_total += 1

        self.message_user(
            request,
            ngettext(
                "Toggled %(count)d suite feature (%(enabled)d enabled, %(disabled)d disabled).",
                "Toggled %(count)d suite features (%(enabled)d enabled, %(disabled)d disabled).",
                toggled_total,
            )
            % {
                "count": toggled_total,
                "enabled": enabled_total,
                "disabled": disabled_total,
            },
            level=messages.SUCCESS,
        )

    def delete_model(self, request, obj):
        """Delete a feature row, surfacing enablement guards as admin errors."""

        try:
            super().delete_model(request, obj)
        except ValidationError as exc:
            messages.error(request, exc.message)
            raise

    def delete_queryset(self, request, queryset):
        """Delete selected features while reporting rows blocked by enablement state."""

        for feature in queryset:
            try:
                feature.delete()
            except ValidationError as exc:
                messages.error(request, f"{feature.display}: {exc.message}")
                raise

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:feature_id>/toggle/",
                self.admin_site.admin_view(self.toggle_feature),
                name="features_feature_toggle",
            ),
        ]
        return custom_urls + urls

    def get_fieldsets(self, request, obj=None):
        """Append dynamic parameter fields for features that define editable params."""

        fieldsets = list(super().get_fieldsets(request, obj))
        if obj is None:
            return fieldsets

        parameter_fields = [
            f"{FeatureAdminForm.PARAM_FIELD_PREFIX}{definition.key}"
            for definition in get_feature_parameter_definitions(obj.slug)
        ]
        if parameter_fields:
            fieldsets.append((_("Feature parameters"), {"fields": tuple(parameter_fields)}))
        return fieldsets

    def get_form(self, request, obj=None, **kwargs):
        """Return a form class that tolerates dynamic parameter fields in fieldsets."""

        model_field_names = {field.name for field in self.model._meta.get_fields()}
        form_class = kwargs.get("form") or getattr(self, "form", None)
        declared_field_names = set(getattr(form_class, "declared_fields", {}).keys())
        allowed_fields = model_field_names | declared_field_names

        field_names = kwargs.get("fields")
        if field_names is None:
            field_names = flatten_fieldsets(self.get_fieldsets(request, obj))
        if field_names:
            kwargs["fields"] = [name for name in field_names if name in allowed_fields]

        try:
            return super().get_form(request, obj, **kwargs)
        except FieldError:
            kwargs.pop("fields", None)
            return super().get_form(request, obj, **kwargs)

    def get_formsets_with_inlines(self, request, obj=None):
        """Skip inline formsets on POST when no inline management payload is submitted."""

        for formset, inline in super().get_formsets_with_inlines(request, obj=obj):
            if request.method == "POST":
                prefix = formset.get_default_prefix()
                if f"{prefix}-TOTAL_FORMS" not in request.POST:
                    continue
            yield formset, inline

    def save_model(self, request, obj, form, change):
        """Persist both model fields and dynamic parameter values."""

        if isinstance(form, FeatureAdminForm):
            set_feature_parameter_values(obj, form.cleaned_parameter_values())
        super().save_model(request, obj, form, change)
        if obj.slug == CELERY_WORKERS_FEATURE_SLUG:
            worker_count, restarted = sync_celery_workers_from_feature()
            if restarted:
                self.message_user(
                    request,
                    _("Celery worker count updated to %(count)d and service restarted.")
                    % {"count": worker_count},
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    _("Celery worker count updated to %(count)d, but service restart failed.")
                    % {"count": worker_count},
                    level=messages.WARNING,
                )

    def toggle_feature(self, request, feature_id: int):
        feature = self.get_object(request, feature_id)
        if feature is None:
            return HttpResponseRedirect(reverse("admin:features_feature_changelist"))
        if not self.has_change_permission(request, obj=feature):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:features_feature_change", args=[feature.pk]))

        feature.set_enabled(not feature.is_enabled)
        status = _("enabled") if feature.is_enabled else _("disabled")
        messages.success(
            request,
            _("%(feature)s is now %(status)s.")
            % {"feature": feature.display, "status": status},
        )
        redirect_to = request.META.get("HTTP_REFERER")
        if redirect_to and url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return HttpResponseRedirect(redirect_to)
        return HttpResponseRedirect(reverse("admin:features_feature_changelist"))
