"""Seed Playwright Automation suite feature."""

from django.db import migrations


FEATURE_SLUG = "playwright-automation"


def seed_playwright_automation_suite_feature(apps, schema_editor):
    """Create or update the Playwright Automation suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")

    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Playwright Automation",
            "summary": (
                "Enable Playwright runtime automation entry points used by admin actions, "
                "and browser launch checks."
            ),
            "is_enabled": True,
            "admin_requirements": ("Run Playwright browser tests from Django admin."),
            "service_requirements": (
                "Allow Playwright runtime launchers when global automation policy "
                "is enabled."
            ),
            "admin_views": [
                "admin:playwright_playwrightbrowser_changelist",
            ],
            "service_views": [
                "apps.playwright.models.PlaywrightBrowser.create_driver",
            ],
            "code_locations": [
                "apps/playwright/admin.py",
                "apps/playwright/models.py",
            ],
            "metadata": {
                "runtime_paths": [
                    "apps.playwright.models.PlaywrightBrowser.create_driver",
                ],
                "admin_paths": [
                    "admin:playwright_playwrightbrowser_changelist",
                ],
                "node_feature_requirements": [
                    "playwright-browser-chromium",
                    "playwright-browser-firefox",
                    "playwright-browser-webkit",
                ],
            },
            "source": "mainstream",
        },
    )


def unseed_playwright_automation_suite_feature(apps, schema_editor):
    """Delete the Playwright Automation suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0045_seed_shortcut_management_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_playwright_automation_suite_feature,
            reverse_code=unseed_playwright_automation_suite_feature,
        ),
    ]
