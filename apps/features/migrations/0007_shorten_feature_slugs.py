from django.db import migrations


def shorten_suite_feature_names(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")

    updates = (
        (
            "deploy-lightsail-cli-auth-bootstrap",
            "lightsail-deployer",
            "Lightsail Deployer",
        ),
        (
            "standard-charge-point",
            "cp-auto-enrollment",
            "CP Auto-enrollment",
        ),
    )

    for old_slug, new_slug, new_display in updates:
        legacy = Feature.objects.filter(slug=old_slug).order_by("pk").first()
        if legacy is None:
            existing = Feature.objects.filter(slug=new_slug).order_by("pk").first()
            if existing is not None:
                existing.display = new_display
                existing.save(update_fields=["display", "updated_at"])
            continue

        duplicate = Feature.objects.filter(slug=new_slug).exclude(pk=legacy.pk).order_by("pk").first()
        if duplicate is not None:
            duplicate.delete()

        legacy.slug = new_slug
        legacy.display = new_display
        legacy.save(update_fields=["slug", "display", "updated_at"])


def restore_suite_feature_names(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")

    updates = (
        ("lightsail-deployer", "deploy-lightsail-cli-auth-bootstrap", "Deploy Lightsail CLI Auth Bootstrap"),
        ("cp-auto-enrollment", "standard-charge-point", "Standard Charge Point Creation"),
    )

    for old_slug, new_slug, new_display in updates:
        current = Feature.objects.filter(slug=old_slug).order_by("pk").first()
        if current is None:
            existing = Feature.objects.filter(slug=new_slug).order_by("pk").first()
            if existing is not None:
                existing.display = new_display
                existing.save(update_fields=["display", "updated_at"])
            continue

        duplicate = Feature.objects.filter(slug=new_slug).exclude(pk=current.pk).order_by("pk").first()
        if duplicate is not None:
            duplicate.delete()

        current.slug = new_slug
        current.display = new_display
        current.save(update_fields=["slug", "display", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0006_feature_baseline_version"),
    ]

    operations = [
        migrations.RunPython(shorten_suite_feature_names, restore_suite_feature_names),
    ]
