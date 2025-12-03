import django.db.models.deletion
from django.db import migrations, models


def _assign_language_references(apps, schema_editor):
    Language = apps.get_model("locale", "Language")
    Charger = apps.get_model("ocpp", "Charger")

    language_map = {
        (lang.code or "").strip(): lang.pk
        for lang in Language.objects.filter(is_deleted=False)
    }
    default_language_id = (
        Language.objects.filter(is_deleted=False, is_default=True)
        .values_list("pk", flat=True)
        .first()
    )

    for charger in Charger.objects.all():
        code = (getattr(charger, "language_code", "") or "").strip()
        target_id = language_map.get(code)
        if target_id is None and not code:
            target_id = default_language_id
        if target_id:
            charger.language_id = target_id
            charger.save(update_fields=["language"])


def _restore_language_codes(apps, schema_editor):
    Charger = apps.get_model("ocpp", "Charger")
    for charger in Charger.objects.all():
        language = getattr(charger, "language", None)
        charger.language_code = (language.code if language else "") or ""
        charger.save(update_fields=["language_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("locale", "0002_seed_languages"),
        ("ocpp", "0002_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="charger",
            old_name="language",
            new_name="language_code",
        ),
        migrations.AddField(
            model_name="charger",
            name="language",
            field=models.ForeignKey(
                blank=True,
                help_text="Preferred language for the public landing page.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="chargers",
                to="locale.language",
                verbose_name="Language",
            ),
        ),
        migrations.RunPython(
            _assign_language_references,
            reverse_code=_restore_language_codes,
        ),
        migrations.RemoveField(
            model_name="charger",
            name="language_code",
        ),
    ]
