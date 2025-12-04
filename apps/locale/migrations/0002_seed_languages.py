from django.db import migrations


LANGUAGE_DATA = [
    {
        "code": "en",
        "english_name": "English",
        "native_name": "English",
        "is_default": True,
    },
    {
        "code": "es",
        "english_name": "Spanish (Latin America)",
        "native_name": "Español (Latinoamérica)",
        "is_default": False,
    },
    {
        "code": "it",
        "english_name": "Italian",
        "native_name": "Italiano",
        "is_default": False,
    },
    {
        "code": "de",
        "english_name": "German",
        "native_name": "Deutsch",
        "is_default": False,
    },
]


def seed_languages(apps, schema_editor):
    Language = apps.get_model("locale", "Language")
    for index, entry in enumerate(LANGUAGE_DATA, start=1):
        Language.objects.update_or_create(
            code=entry["code"],
            defaults={
                "english_name": entry["english_name"],
                "native_name": entry["native_name"],
                "is_default": entry["is_default"],
                "is_seed_data": True,
                "is_deleted": False,
            },
        )


def unseed_languages(apps, schema_editor):
    Language = apps.get_model("locale", "Language")
    Language.objects.filter(code__in=[entry["code"] for entry in LANGUAGE_DATA]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("locale", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed_languages, reverse_code=unseed_languages)]
