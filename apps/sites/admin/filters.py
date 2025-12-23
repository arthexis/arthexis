from django.contrib import admin
from django.core.exceptions import FieldError
from django.utils.translation import gettext_lazy as _
from django.apps import apps as django_apps


class _BooleanAttributeListFilter(admin.SimpleListFilter):
    """Filter helper for boolean attributes on :class:`~django.contrib.sites.models.Site`."""

    field_name: str

    def lookups(self, request, model_admin):  # pragma: no cover - admin UI
        return (("1", _("Yes")), ("0", _("No")))

    def queryset(self, request, queryset):
        value = self.value()
        if value not in {"0", "1"}:
            return queryset
        expected = value == "1"
        try:
            return queryset.filter(**{self.field_name: expected})
        except FieldError:  # pragma: no cover - defensive when fields missing
            return queryset


class ManagedSiteListFilter(_BooleanAttributeListFilter):
    title = _("Managed by local NGINX")
    parameter_name = "managed"
    field_name = "managed"


class RequireHttpsListFilter(_BooleanAttributeListFilter):
    title = _("Require HTTPS")
    parameter_name = "require_https"
    field_name = "require_https"


class ApplicationInstalledListFilter(admin.SimpleListFilter):
    title = _("Installed state")
    parameter_name = "installed"

    def lookups(self, request, model_admin):  # pragma: no cover - admin UI
        return (("1", _("Installed")), ("0", _("Not installed")))

    def queryset(self, request, queryset):  # pragma: no cover - admin UI
        value = self.value()
        if value not in {"0", "1"}:
            return queryset

        installed_labels = set()
        installed_names = set()
        for config in django_apps.get_app_configs():
            installed_labels.add(config.label)
            installed_names.add(config.name)
            installed_names.add(config.name.rsplit(".", 1)[-1])

        installed_values = installed_labels | installed_names
        if value == "1":
            return queryset.filter(name__in=installed_values)
        return queryset.exclude(name__in=installed_values)
