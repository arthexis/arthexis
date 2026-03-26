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
                "browser launch checks and on-demand screenshot runs."
            ),
            "is_enabled": True,
            "admin_requirements": (
                "Run Playwright browser tests and trigger screenshot schedules from Django admin."
            ),
            "service_requirements": (
                "Allow Playwright runtime launchers and admin-triggered screenshot runs when "
                "global automation policy is enabled."
            ),
            "admin_views": [
                "admin:playwright_playwrightbrowser_changelist",
                "admin:playwright_websitescreenshotschedule_changelist",
            ],
            "service_views": [
                "apps.playwright.models.PlaywrightBrowser.create_driver",
                "apps.playwright.models.execute_website_screenshot_schedule",
            ],
            "code_locations": [
                "apps/playwright/admin.py",
                "apps/playwright/models.py",
                "apps/tasks/tasks.py",
            ],
            "metadata": {
                "runtime_paths": [
                    "apps.playwright.models.PlaywrightBrowser.create_driver",
                    "apps.playwright.models.execute_website_screenshot_schedule",
                ],
                "admin_paths": [
                    "admin:playwright_playwrightbrowser_changelist",
                    "admin:playwright_websitescreenshotschedule_changelist",
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
