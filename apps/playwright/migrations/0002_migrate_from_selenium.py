from django.db import migrations


def copy_selenium_data(apps, schema_editor):
    del schema_editor
    SeleniumBrowser = apps.get_model("selenium", "SeleniumBrowser")
    SeleniumScript = apps.get_model("selenium", "SeleniumScript")
    SeleniumSessionCookie = apps.get_model("selenium", "SessionCookie")

    PlaywrightBrowser = apps.get_model("playwright", "PlaywrightBrowser")
    PlaywrightScript = apps.get_model("playwright", "PlaywrightScript")
    PlaywrightSessionCookie = apps.get_model("playwright", "SessionCookie")

    for row in SeleniumBrowser.objects.all():
        PlaywrightBrowser.objects.update_or_create(
            name=row.name,
            defaults={
                "engine": row.engine,
                "mode": row.mode,
                "binary_path": row.binary_path,
                "is_default": row.is_default,
                "is_seed_data": row.is_seed_data,
                "is_user_data": row.is_user_data,
                "is_deleted": row.is_deleted,
            },
        )

    for row in SeleniumScript.objects.all():
        PlaywrightScript.objects.update_or_create(
            name=row.name,
            defaults={
                "description": row.description,
                "start_url": row.start_url,
                "script": row.script,
                "python_path": row.python_path,
                "is_seed_data": row.is_seed_data,
                "is_user_data": row.is_user_data,
                "is_deleted": row.is_deleted,
            },
        )

    for row in SeleniumSessionCookie.objects.all():
        PlaywrightSessionCookie.objects.update_or_create(
            name=row.name,
            defaults={
                "source": row.source,
                "cookies": row.cookies,
                "state": row.state,
                "last_used_at": row.last_used_at,
                "last_validated_at": row.last_validated_at,
                "expires_at": row.expires_at,
                "rejection_count": row.rejection_count,
                "last_rejection_reason": row.last_rejection_reason,
                "group_id": row.group_id,
                "user_id": row.user_id,
                "is_seed_data": row.is_seed_data,
                "is_user_data": row.is_user_data,
                "is_deleted": row.is_deleted,
            },
        )


def noop_reverse(apps, schema_editor):
    del apps, schema_editor


class Migration(migrations.Migration):
    dependencies = [
        ("playwright", "0001_initial"),
        ("selenium", "0008_remove_sessioncookie_selenium_sessioncookie_owner_exclusive_and_more"),
    ]

    operations = [
        migrations.RunPython(copy_selenium_data, noop_reverse),
    ]
