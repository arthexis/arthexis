"""Seed the LLM Summary Automation suite feature."""

from django.db import migrations


LLM_SUMMARY_AUTOMATION_FEATURE_SLUG = "llm-summary-automation"


def seed_llm_summary_automation_suite_feature(apps, schema_editor):
    """Create or update the LLM Summary Automation suite feature definition."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Application = apps.get_model("app", "Application")

    summary_app = Application.objects.filter(name="summary").first()

    Feature.objects.update_or_create(
        slug=LLM_SUMMARY_AUTOMATION_FEATURE_SLUG,
        defaults={
            "display": "LLM Summary Automation",
            "source": "mainstream",
            "summary": (
                "Gate automated LCD log summary generation for scheduler and command entrypoints."
            ),
            "is_enabled": False,
            "main_app": summary_app,
            "node_feature": None,
            "admin_requirements": (
                "Operators should see clear suite-gate messaging in the summary management command "
                "and use --allow-disabled-feature for one-off manual runs when automation is disabled."
            ),
            "public_requirements": "",
            "service_requirements": (
                "Scheduler surface apps.tasks.tasks.generate_lcd_log_summary and runtime helper "
                "apps.summary.services.execute_log_summary_generation must short-circuit automation "
                "unless this suite feature is enabled."
            ),
            "admin_views": [
                "management:summary",
            ],
            "public_views": [],
            "service_views": [
                "apps.tasks.tasks.generate_lcd_log_summary",
                "apps.summary.services.execute_log_summary_generation",
            ],
            "code_locations": [
                "apps/summary/management/commands/summary.py",
                "apps/summary/services.py",
                "apps/tasks/tasks.py",
            ],
            "protocol_coverage": {},
        },
    )


def unseed_llm_summary_automation_suite_feature(apps, schema_editor):
    """Delete the seeded LLM Summary Automation suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=LLM_SUMMARY_AUTOMATION_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0036_seed_ocpp_forwarder_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_llm_summary_automation_suite_feature,
            unseed_llm_summary_automation_suite_feature,
        ),
    ]
