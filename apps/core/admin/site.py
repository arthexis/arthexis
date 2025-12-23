from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.auth.models import Group
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


original_changelist_view = admin.ModelAdmin.changelist_view


def changelist_view_with_object_links(self, request, extra_context=None):
    extra_context = extra_context or {}
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


def get_app_list_with_protocol_forwarder(self, request, app_label=None):
    try:
        Application = django_apps.get_model("app", "Application")
    except LookupError:
        return _original_admin_get_app_list(self, request, app_label=app_label)

    full_list = list(_original_admin_get_app_list(self, request, app_label=None))
    result = full_list

    if app_label:
        result = [entry for entry in result if entry.get("app_label") == app_label]

    ordered_result = []

    for entry in result:
        app_label = entry.get("app_label")
        entry_name = str(app_label or entry.get("name"))

        ordered_entry = entry.copy()
        ordered_entry["name"] = Application.format_display_name(entry_name)
        ordered_result.append(ordered_entry)

    ordered_result.sort(key=lambda entry: (entry.get("name"), entry.get("app_label")))
    return ordered_result


admin.AdminSite.get_app_list = get_app_list_with_protocol_forwarder
