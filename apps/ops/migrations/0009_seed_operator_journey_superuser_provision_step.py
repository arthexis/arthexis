from django.db import migrations

SEEDED_JOURNEY_SLUG = "operator-node-readiness"
SEEDED_STEP_SLUG = "provision-ops-superuser"


def seed_operator_journey_superuser_step(apps, schema_editor):
    """Add the post-role-confirmation superuser provisioning step."""

    OperatorJourney = apps.get_model("ops", "OperatorJourney")
    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")
    journey = OperatorJourney.objects.filter(slug=SEEDED_JOURNEY_SLUG).first()
    if journey is None:
        return

    step = OperatorJourneyStep.objects.filter(
        journey=journey, slug=SEEDED_STEP_SLUG
    ).first()
    if step is None:
        step = OperatorJourneyStep(journey=journey, slug=SEEDED_STEP_SLUG)

    for conflicting_step in (
        OperatorJourneyStep.objects.filter(journey=journey, order__gte=2)
        .exclude(pk=step.pk)
        .order_by("-order", "-id")
    ):
        conflicting_step.order += 1
        conflicting_step.save(update_fields=["order"])

    step.title = "Create operational superuser and security access"
    step.instruction = (
        "After confirming node role/setup, create a dedicated superuser for operations. "
        "Assign one or more security groups, choose random/manual password handling, "
        "and optionally attach GitHub credentials for repository/release/issue management."
    )
    step.help_text = (
        "Complete this step by creating the account, recording credentials securely, "
        "and verifying the account can log in."
    )
    step.iframe_url = "/admin/auth/user/add/"
    step.order = 2
    step.is_active = True
    step.is_seed_data = True
    step.save()


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
        migrations.RunPython(
            seed_operator_journey_superuser_step, unseed_operator_journey_superuser_step
        ),
    ]
