from django.db import migrations


def seed_default_shortcuts(apps, schema_editor):
    DesktopShortcut = apps.get_model("desktop", "DesktopShortcut")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    desktop_feature = NodeFeature.objects.filter(slug="user-desktop").first()

    defaults = [
        {
            "slug": "public-site",
            "desktop_filename": "Arthexis Public Site",
            "name": "Arthexis Public Site",
            "comment": "Open the Arthexis public site",
            "launch_mode": "url",
            "target_url": "http://127.0.0.1:{port}/",
            "icon_name": "web-browser",
            "categories": "Network;WebBrowser;",
            "sort_order": 10,
            "only_staff": False,
        },
        {
            "slug": "admin-console",
            "desktop_filename": "Arthexis Admin Console",
            "name": "Arthexis Admin Console",
            "comment": "Open the Arthexis admin console",
            "launch_mode": "url",
            "target_url": "http://127.0.0.1:{port}/admin/",
            "icon_name": "applications-system",
            "categories": "Office;System;",
            "sort_order": 20,
            "only_staff": True,
        },
    ]

    for payload in defaults:
        shortcut, _ = DesktopShortcut.objects.update_or_create(
            slug=payload["slug"],
            defaults={
                "is_seed_data": True,
                "is_deleted": False,
                "desktop_filename": payload["desktop_filename"],
                "name": payload["name"],
                "comment": payload["comment"],
                "launch_mode": payload["launch_mode"],
                "target_url": payload["target_url"],
                "command": "",
                "icon_name": payload["icon_name"],
                "icon_base64": "",
                "icon_extension": "png",
                "categories": payload["categories"],
                "terminal": False,
                "startup_notify": True,
                "extra_entries": {},
                "condition_expression": "",
                "condition_command": "",
                "require_desktop_ui": True,
                "only_staff": payload["only_staff"],
                "only_superuser": False,
                "is_enabled": True,
                "sort_order": payload["sort_order"],
            },
        )
        if desktop_feature is not None:
            shortcut.required_features.add(desktop_feature)


def unseed_default_shortcuts(apps, schema_editor):
    DesktopShortcut = apps.get_model("desktop", "DesktopShortcut")
    DesktopShortcut.objects.filter(slug__in=["public-site", "admin-console"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("desktop", "0002_desktopshortcut"),
        ("nodes", "0035_add_user_desktop_feature"),
    ]

    operations = [
        migrations.RunPython(seed_default_shortcuts, unseed_default_shortcuts),
    ]
