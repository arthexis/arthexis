from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import FieldError
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

admin.site.unregister(Group)


def _append_operate_as(fieldsets):
    updated = []
    for name, options in fieldsets:
        opts = options.copy()
        fields = opts.get("fields")
        if fields and "is_staff" in fields and "operate_as" not in fields:
            if not isinstance(fields, (list, tuple)):
                fields = list(fields)
            else:
                fields = list(fields)
            fields.append("operate_as")
            opts["fields"] = tuple(fields)
        updated.append((name, opts))
    return tuple(updated)


def _include_require_2fa(fieldsets):
    updated = []
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "is_active" in fields and "require_2fa" not in fields:
            insert_at = fields.index("is_active") + 1
            fields.insert(insert_at, "require_2fa")
            opts["fields"] = tuple(fields)
        updated.append((name, opts))
    return tuple(updated)


def _include_temporary_expiration(fieldsets):
    updated = []
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "is_active" in fields and "temporary_expires_at" not in fields:
            insert_at = fields.index("is_active") + 1
            fields.insert(insert_at, "temporary_expires_at")
            opts["fields"] = tuple(fields)
        updated.append((name, opts))
    return tuple(updated)


def _include_site_template(fieldsets):
    updated = []
    inserted = False
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "groups" in fields and "site_template" not in fields:
            insert_at = fields.index("groups") + 1
            fields.insert(insert_at, "site_template")
            opts["fields"] = tuple(fields)
            inserted = True
        updated.append((name, opts))
    if not inserted:
        updated.append((_("Preferences"), {"fields": ("site_template",)}))
    return tuple(updated)


def _include_site_template_add(fieldsets):
    updated = []
    inserted = False
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "username" in fields and "site_template" not in fields:
            if "temporary_expires_at" in fields:
                insert_at = fields.index("temporary_expires_at") + 1
            else:
                insert_at = len(fields)
            fields.insert(insert_at, "site_template")
            opts["fields"] = tuple(fields)
            inserted = True
        updated.append((name, opts))
    if not inserted:
        updated.append((_("Preferences"), {"fields": ("site_template",)}))
    return tuple(updated)



def _parse_prefilter_id_values(raw_value):
    """Return normalized selected primary-key values from query-string input."""
    if not raw_value:
        return []
    values = []
    for value in str(raw_value).split(","):
        normalized = value.strip()
        if normalized:
            values.append(normalized)
    return values


def _parse_prefilter_lookups(raw_value):
    """Return allowed relation lookup paths from query-string input."""
    if not raw_value:
        return []
    lookups = []
    for value in str(raw_value).split(","):
        lookup = value.strip()
        if lookup.endswith("__id__in") and lookup:
            lookups.append(lookup)
    return list(dict.fromkeys(lookups))


def _get_related_selection_prefilter_query(request):
    """Build a queryset filter for related-model prefilter query parameters."""
    selected_ids = _parse_prefilter_id_values(request.GET.get("__selected_ids"))
    relation_lookups = _parse_prefilter_lookups(
        request.GET.get("__relation_lookups")
    )
    if not selected_ids or not relation_lookups:
        return None
    prefilter_query = Q()
    for lookup in relation_lookups:
        prefilter_query |= Q(**{lookup: selected_ids})
    return prefilter_query


original_changelist_view = admin.ModelAdmin.changelist_view


def changelist_view_with_object_links(self, request, extra_context=None):
    extra_context = extra_context or {}

    if any(
        key in request.GET
        for key in ("__selected_ids", "__relation_lookups", "__source_model")
    ):
        cleaned_query = request.GET.copy()
        cleaned_query.pop("__selected_ids", None)
        cleaned_query.pop("__relation_lookups", None)
        cleaned_query.pop("__source_model", None)
        request.GET = cleaned_query
        request.META["QUERY_STRING"] = cleaned_query.urlencode()

    count = self.model._default_manager.count()
    if 1 <= count <= 4:
        links = []
        for obj in self.model._default_manager.all():
            url = reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
                args=[obj.pk],
            )
            links.append({"url": url, "label": str(obj)})
        extra_context["global_object_links"] = links
    return original_changelist_view(self, request, extra_context=extra_context)


admin.ModelAdmin.changelist_view = changelist_view_with_object_links


_original_admin_get_app_list = admin.AdminSite.get_app_list


def _get_application_priority_map(Application):
    """Return a map of application labels to configured priority numbers."""
    return {
        name: priority
        for name, priority in Application.objects.filter(order__isnull=False).values_list(
            "name", "order"
        )
    }


def _priority_suffix(index: int) -> str:
    """Convert a zero-based index into alphabetical suffixes (a, b, ..., aa)."""
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    result = ""
    current = index
    while True:
        current, remainder = divmod(current, len(alphabet))
        result = alphabet[remainder] + result
        if current == 0:
            break
        current -= 1
    return result


def get_app_list_with_protocol_forwarder(self, request, app_label=None):
    """Sort the admin app list using optional Application priorities first."""
    try:
        Application = django_apps.get_model("app", "Application")
    except LookupError:
        return _original_admin_get_app_list(self, request, app_label=app_label)

    full_list = list(_original_admin_get_app_list(self, request, app_label=None))
    result = full_list

    if app_label:
        result = [entry for entry in result if entry.get("app_label") == app_label]

    priority_map = _get_application_priority_map(Application)

    ordered_result = []
    grouped_priorities = {}

    for entry in result:
        current_app_label = entry.get("app_label")
        entry_name = str(current_app_label or entry.get("name"))
        priority = priority_map.get(current_app_label)

        ordered_entry = entry.copy()
        ordered_entry["name"] = Application.format_display_name(entry_name)
        ordered_entry["_sort_priority"] = priority
        ordered_result.append(ordered_entry)

        if priority is not None:
            grouped_priorities.setdefault(priority, []).append(ordered_entry)

    for priority, entries in grouped_priorities.items():
        if len(entries) <= 1:
            continue
        entries.sort(key=lambda item: (item.get("name"), item.get("app_label")))
        for index, item in enumerate(entries):
            item["name"] = f"{priority}{_priority_suffix(index)}. {item['name']}"

    ordered_result.sort(
        key=lambda entry: (
            entry.get("_sort_priority") is None,
            entry.get("_sort_priority") if entry.get("_sort_priority") is not None else 0,
            entry.get("name"),
            entry.get("app_label"),
        )
    )
    for entry in ordered_result:
        entry.pop("_sort_priority", None)
    return ordered_result


admin.AdminSite.get_app_list = get_app_list_with_protocol_forwarder

_original_get_queryset = admin.ModelAdmin.get_queryset


def get_queryset_with_related_selection_prefilter(self, request):
    """Filter changelist querysets using selected related-record context parameters."""
    queryset = _original_get_queryset(self, request)
    prefilter_query = _get_related_selection_prefilter_query(request)
    if prefilter_query is None:
        return queryset
    try:
        return queryset.filter(prefilter_query).distinct()
    except (FieldError, TypeError, ValueError):
        return queryset.none()


admin.ModelAdmin.get_queryset = get_queryset_with_related_selection_prefilter

