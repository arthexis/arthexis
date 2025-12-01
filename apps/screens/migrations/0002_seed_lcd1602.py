from django.db import migrations

from apps.migration_utils import get_model


LCD_SLUG = "lcd-1602"


def seed_lcd_screen(apps, schema_editor):
    DeviceScreen = get_model(apps, "screens", "DeviceScreen", allow_missing=True)

    if DeviceScreen is None:
        return

    defaults = {
        "name": "I2C LCD1602",
        "category": "lcd",
        "skin": "pcf8574",
        "columns": 16,
        "rows": 2,
        "is_seed_data": True,
        "is_user_data": False,
        "is_deleted": False,
    }

    screen, created = DeviceScreen.all_objects.get_or_create(
        slug=LCD_SLUG,
        defaults=defaults,
    )

    if not created:
        for field, value in defaults.items():
            setattr(screen, field, value)
        screen.save(update_fields=list(defaults.keys()))


def remove_lcd_screen(apps, schema_editor):
    DeviceScreen = get_model(apps, "screens", "DeviceScreen", allow_missing=True)

    if DeviceScreen is None:
        return

    DeviceScreen.all_objects.filter(slug=LCD_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("screens", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_lcd_screen, remove_lcd_screen),
    ]
