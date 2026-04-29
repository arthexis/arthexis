from django.db import migrations

PRODUCT_DEVELOPER_JOURNEY_SLUG = "product-developer-github-access"
GITHUB_SETUP_STEP_SLUG = "setup-github-token"


def remove_product_developer_github_setup_step(apps, schema_editor):
    """Remove the seeded GitHub credential setup task and normalize ordering."""

    OperatorJourney = apps.get_model("ops", "OperatorJourney")
    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")

    journey = OperatorJourney.objects.filter(slug=PRODUCT_DEVELOPER_JOURNEY_SLUG).first()
    if journey is None:
        return

    OperatorJourneyStep.objects.filter(
        journey=journey,
        slug=GITHUB_SETUP_STEP_SLUG,
    ).delete()

    remaining_steps = list(
        OperatorJourneyStep.objects.filter(journey=journey).order_by("order", "id")
    )
    for position, step in enumerate(remaining_steps, start=1):
        if step.order != position:
            step.order = position
            step.save(update_fields=["order"])


def noop_reverse(apps, schema_editor):
    """Preserve user-managed journey state on reverse."""


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0011_seed_product_developer_github_supervision_steps"),
    ]

    operations = [
        migrations.RunPython(remove_product_developer_github_setup_step, noop_reverse),
    ]
