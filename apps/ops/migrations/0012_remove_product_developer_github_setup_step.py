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
    if not remaining_steps:
        return

    max_order = max(step.order for step in remaining_steps) + len(remaining_steps)
    temp_steps = []
    for position, step in enumerate(remaining_steps, start=1):
        step.order = max_order + position
        temp_steps.append(step)

    OperatorJourneyStep.objects.bulk_update(temp_steps, ["order"])

    for position, step in enumerate(remaining_steps, start=1):
        step.order = position

    OperatorJourneyStep.objects.bulk_update(remaining_steps, ["order"])


def noop_reverse(apps, schema_editor):
    """Preserve user-managed journey state on reverse."""


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0011_seed_product_developer_github_supervision_steps"),
    ]

    operations = [
        migrations.RunPython(remove_product_developer_github_setup_step, noop_reverse),
    ]
