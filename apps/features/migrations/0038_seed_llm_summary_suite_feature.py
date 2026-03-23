"""Seed the LLM Summary suite feature and retire legacy automation slug."""

from django.db import migrations


NEW_SLUG = "llm-summary-suite"
LEGACY_SLUG = "llm-summary-automation"
NODE_FEATURE_SLUG = "llm-summary"


def seed_llm_summary_suite_feature(apps, schema_editor):
    """Create or update the LLM Summary suite feature with node linkage and parameters."""

    del schema_editor
    Application = apps.get_model("app", "Application")
    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    summary_app = Application.objects.filter(name="summary").first()
    llm_node_feature = NodeFeature.objects.filter(slug=NODE_FEATURE_SLUG).first()
    defaults = {
        "display": "LLM Summary Suite",
        "source": "mainstream",
        "summary": (
            "Gate LCD log summarization and centralize model configuration for node execution."
        ),
        "is_enabled": True,
        "main_app": summary_app,
        "node_feature": llm_node_feature,
        "admin_requirements": (
            "Provide a Configure wizard that reports missing prerequisites and allows model "
            "parameter updates from one place."
        ),
        "public_requirements": "",
        "service_requirements": (
            "Summary generation tasks should run only when this suite feature is enabled and "
            "the llm-summary node feature prerequisites are satisfied."
        ),
        "admin_views": [
            "admin:summary_llmsummaryconfig_wizard",
            "admin:summary_llmsummaryconfig_changelist",
        ],
        "public_views": [],
        "service_views": [
            "apps.summary.services.execute_log_summary_generation",
            "apps.summary.tasks.generate_lcd_log_summary",
        ],
        "code_locations": [
            "apps/summary/admin.py",
            "apps/summary/services.py",
            "apps/nodes/models/features.py",
        ],
        "protocol_coverage": {},
        "metadata": {
            "parameters": {
                "model_path": "",
                "model_command": "",
                "timeout_seconds": "240",
            }
        },
    }

    legacy = Feature.objects.filter(slug=LEGACY_SLUG).first()
    if legacy:
        Feature.objects.update_or_create(slug=NEW_SLUG, defaults=defaults)
        Feature.objects.filter(pk=legacy.pk).delete()
        return

    Feature.objects.update_or_create(slug=NEW_SLUG, defaults=defaults)


def unseed_llm_summary_suite_feature(apps, schema_editor):
    """Delete the LLM Summary suite feature and restore the legacy automation slug."""

    del schema_editor
    Application = apps.get_model("app", "Application")
    Feature = apps.get_model("features", "Feature")

    Feature.objects.filter(slug=NEW_SLUG).delete()
    summary_app = Application.objects.filter(name="summary").first()
    Feature.objects.update_or_create(
        slug=LEGACY_SLUG,
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
            "admin_views": ["management:summary"],
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


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0037_seed_llm_summary_automation_suite_feature"),
        ("nodes", "0040_merge_20260307_2002"),
    ]

    operations = [
        migrations.RunPython(
            seed_llm_summary_suite_feature,
            unseed_llm_summary_suite_feature,
        ),
    ]
