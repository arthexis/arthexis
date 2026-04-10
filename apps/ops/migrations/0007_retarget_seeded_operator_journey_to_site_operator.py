from django.db import migrations

SITE_OPERATOR_GROUP_NAME = "Site Operator"
SEEDED_JOURNEY_SLUG = "operator-node-readiness"


def retarget_seeded_journey_to_site_operator(apps, schema_editor):
    """Assign the seeded operator node readiness journey to Site Operator."""

    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    OperatorJourney = apps.get_model("ops", "OperatorJourney")

    site_operator_group, _ = SecurityGroup.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)
    OperatorJourney.objects.filter(
        slug=SEEDED_JOURNEY_SLUG,
        is_seed_data=True,
    ).update(security_group=site_operator_group)


def noop_reverse(apps, schema_editor):
    """Leave journey ownership unchanged when reversing this migration."""


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0006_seed_operator_journey_first_step"),
    ]

    operations = [
        migrations.RunPython(retarget_seeded_journey_to_site_operator, noop_reverse),
    ]
