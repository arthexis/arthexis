from django.db import migrations
from django.db.models import Q


OLD_PREFIX = "/readme"
NEW_PREFIX = "/read"


def _rewrite_path(path: str | None, old_prefix: str, new_prefix: str) -> str | None:
    if not path or not path.startswith(old_prefix):
        return None
    remainder = path[len(old_prefix) :]
    if remainder and not remainder.startswith("/"):
        remainder = f"/{remainder}"
    if not remainder:
        remainder = "/"
    return f"{new_prefix}{remainder}"


def forwards(apps, schema_editor):
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")
    RoleLanding = apps.get_model("pages", "RoleLanding")
    LandingLead = apps.get_model("pages", "LandingLead")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    module_replacements: dict[int, int] = {}

    for module in Module.objects.filter(path__startswith=OLD_PREFIX):
        new_path = _rewrite_path(module.path, OLD_PREFIX, NEW_PREFIX)
        if not new_path or new_path == module.path:
            continue

        duplicate = (
            Module.objects.filter(node_role=module.node_role, path=new_path)
            .exclude(pk=module.pk)
            .first()
        )

        if duplicate:
            updated = False
            if module.menu and module.menu != duplicate.menu:
                duplicate.menu = module.menu
                updated = True
            if module.is_default != duplicate.is_default:
                duplicate.is_default = module.is_default
                updated = True
            module_favicon = getattr(module.favicon, "name", "")
            duplicate_favicon = getattr(duplicate.favicon, "name", "")
            if module_favicon and module_favicon != duplicate_favicon:
                duplicate.favicon = module_favicon
                updated = True
            if module.application_id and module.application_id != duplicate.application_id:
                duplicate.application_id = module.application_id
                updated = True

            if updated:
                duplicate.save()

            module_replacements[module.pk] = duplicate.pk

            if not module.is_deleted:
                module.is_deleted = True
                module.save(update_fields=["is_deleted"])
            continue

        module.path = new_path
        module.save(update_fields=["path"])

    landing_filters = Q(path__startswith=OLD_PREFIX)
    if module_replacements:
        landing_filters |= Q(module_id__in=module_replacements.keys())

    landings = Landing.objects.filter(landing_filters)

    for landing in landings:
        if landing.is_deleted:
            continue

        target_module_id = module_replacements.get(landing.module_id, landing.module_id)
        new_path = _rewrite_path(landing.path, OLD_PREFIX, NEW_PREFIX) or landing.path

        duplicate = (
            Landing.objects.filter(module_id=target_module_id, path=new_path)
            .exclude(pk=landing.pk)
            .first()
        )

        if duplicate:
            updated = False
            if landing.label and landing.label != duplicate.label:
                duplicate.label = landing.label
                updated = True
            if landing.enabled != duplicate.enabled:
                duplicate.enabled = landing.enabled
                updated = True
            if landing.track_leads != duplicate.track_leads:
                duplicate.track_leads = landing.track_leads
                updated = True
            if landing.description and landing.description != duplicate.description:
                duplicate.description = landing.description
                updated = True

            if updated:
                duplicate.save()

            RoleLanding.objects.filter(landing=landing).update(landing=duplicate)
            LandingLead.objects.filter(landing=landing).update(landing=duplicate)
            SiteBadge.objects.filter(landing_override=landing).update(landing_override=duplicate)

            if not landing.is_deleted:
                landing.is_deleted = True
                landing.save(update_fields=["is_deleted"])

            continue

        if landing.module_id != target_module_id:
            landing.module_id = target_module_id
        if new_path != landing.path:
            landing.path = new_path

        landing.save(update_fields=["module", "path"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0024_clear_landing_leads"),
    ]

    operations = [
        migrations.RunPython(forwards, noop),
    ]
