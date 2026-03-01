from django.apps import apps
from django.conf import settings
from django.db import migrations


def _get_content_types(content_type_model):
    """Return content types for default admin favorites when models exist."""

    targets = (
        ("ocpp", "charger"),
        ("cards", "rfid"),
        ("links", "reference"),
    )
    content_types = []
    for app_label, model in targets:
        content_type = content_type_model.objects.filter(
            app_label=app_label,
            model=model,
        ).first()
        if content_type is not None:
            content_types.append(content_type)
    return content_types


def seed_admin_default_favorites(apps_registry, schema_editor):
    """Seed admin favorites for Charge Points, RFIDs, and References."""

    del schema_editor
    user_model = apps_registry.get_model(settings.AUTH_USER_MODEL)
    favorite_model = apps_registry.get_model("locals", "Favorite")
    content_type_model = apps_registry.get_model("contenttypes", "ContentType")

    admin_user = user_model.objects.filter(username="admin").first()
    if admin_user is None:
        return

    content_types = _get_content_types(content_type_model)
    for priority, content_type in enumerate(content_types):
        favorite_model.objects.get_or_create(
            user_id=admin_user.pk,
            content_type_id=content_type.pk,
            defaults={
                "priority": priority,
                "user_data": True,
                "is_seed_data": True,
            },
        )


def unseed_admin_default_favorites(apps_registry, schema_editor):
    """Remove seeded default admin favorites created by this migration."""

    del schema_editor
    user_model = apps_registry.get_model(settings.AUTH_USER_MODEL)
    favorite_model = apps_registry.get_model("locals", "Favorite")
    content_type_model = apps_registry.get_model("contenttypes", "ContentType")

    admin_user = user_model.objects.filter(username="admin").first()
    if admin_user is None:
        return

    content_types = _get_content_types(content_type_model)
    content_type_ids = [content_type.pk for content_type in content_types]
    if not content_type_ids:
        return

    favorite_model.objects.filter(
        user_id=admin_user.pk,
        content_type_id__in=content_type_ids,
        is_seed_data=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("locals", "0002_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(seed_admin_default_favorites, unseed_admin_default_favorites),
    ]
