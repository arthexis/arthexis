from django.db import migrations

SEEDED_JOURNEY_SLUG = "operator-node-readiness"
SEEDED_STEP_SLUG = "validate-local-node-role"


def refresh_seeded_validate_role_guidance(apps, schema_editor):
    """Refresh seeded role-validation guidance with script-first instructions."""

    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")
    OperatorJourneyStep.objects.filter(
        journey__slug=SEEDED_JOURNEY_SLUG,
        slug=SEEDED_STEP_SLUG,
        is_seed_data=True,
    ).update(
        title="Validate local node setup and role before continuing",
        instruction=(
            "Confirm this node runtime setup is correct before proceeding. "
            "If role or setup is wrong, apply changes with configure/install scripts and restart."
        ),
        help_text=(
            "Use the setup summary shown in this step. "
            "Do not use Nodes records to change active runtime role."
        ),
    )


def noop_reverse(apps, schema_editor):
    """Do not rewrite user-edited journey step content on migration reverse."""


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0007_retarget_seeded_operator_journey_to_site_operator"),
    ]

    operations = [
        migrations.RunPython(refresh_seeded_validate_role_guidance, noop_reverse),
    ]
