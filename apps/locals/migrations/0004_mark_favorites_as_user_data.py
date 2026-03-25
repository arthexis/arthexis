from django.db import migrations


def mark_favorites_as_user_data(apps, schema_editor):
    Favorite = apps.get_model("locals", "Favorite")
    Favorite.objects.update(user_data=True)
    Favorite.objects.update(is_user_data=True)


def unmark_favorites_as_user_data(apps, schema_editor):
    Favorite = apps.get_model("locals", "Favorite")
    Favorite.objects.update(user_data=False)
    Favorite.objects.update(is_user_data=False)


class Migration(migrations.Migration):
    dependencies = [
        ("locals", "0003_seed_admin_default_favorites"),
    ]

    operations = [
        migrations.RunPython(
            mark_favorites_as_user_data,
            reverse_code=unmark_favorites_as_user_data,
        ),
    ]
