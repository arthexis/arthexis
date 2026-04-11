from django.db import migrations

SEEDED_JOURNEY_SLUG = "operator-node-readiness"
SEEDED_STEP_SLUG = "provision-ops-superuser"


def seed_operator_journey_superuser_step(apps, schema_editor):
    """Add the post-role-confirmation superuser provisioning step."""

    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")
    OperatorJourneyStep.objects.filter(
        journey__slug=SEEDED_JOURNEY_SLUG,
        slug=SEEDED_STEP_SLUG,
        is_seed_data=True,
    ).delete()
    OperatorJourneyStep.objects.create(
        journey=apps.get_model("ops", "OperatorJourney").objects.get(slug=SEEDED_JOURNEY_SLUG),
        slug=SEEDED_STEP_SLUG,
        title="Create operational superuser and security access",
        instruction=(
            "After confirming node role/setup, create a dedicated superuser for operations. "
            "Assign one or more security groups, choose random/manual password handling, "
            "and optionally attach GitHub credentials for repository/release/issue management."
        ),
        help_text=(
            "Complete this step by creating the account, recording credentials securely, "
            "and verifying the account can log in."
        ),
        iframe_url="/admin/auth/user/add/",
        order=2,
        is_active=True,
        is_seed_data=True,
    )


def unseed_operator_journey_superuser_step(apps, schema_editor):
    """Remove only the seeded superuser-provision step."""

    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")
    OperatorJourneyStep.objects.filter(
        journey__slug=SEEDED_JOURNEY_SLUG,
        slug=SEEDED_STEP_SLUG,
        is_seed_data=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0008_refresh_seeded_validate_role_guidance"),
    ]

    operations = [
        migrations.RunPython(seed_operator_journey_superuser_step, unseed_operator_journey_superuser_step),
    ]
