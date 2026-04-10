from django.db import migrations

NETWORK_OPERATOR_GROUP_NAME = "Network Operator"


def seed_operator_journey(apps, schema_editor):
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    OperatorJourney = apps.get_model("ops", "OperatorJourney")
    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")

    security_group, _ = SecurityGroup.objects.get_or_create(name=NETWORK_OPERATOR_GROUP_NAME)
    journey, _ = OperatorJourney.objects.get_or_create(
        slug="operator-node-readiness",
        defaults={
            "name": "Operator Node Readiness",
            "description": "Guided operational readiness checks for node operators.",
            "security_group": security_group,
            "priority": 10,
            "is_active": True,
        },
    )
    if journey.security_group_id != security_group.id:
        journey.security_group = security_group
        journey.save(update_fields=["security_group"])

    OperatorJourneyStep.objects.get_or_create(
        journey=journey,
        slug="validate-local-node-role",
        defaults={
            "title": "Validate local node role and restart if changed",
            "instruction": (
                "Confirm this node uses the correct role before proceeding. "
                "If the role is wrong, select the target role and restart the instance, "
                "then return and mark this step complete."
            ),
            "help_text": (
                "Use Nodes → Nodes to inspect the local node role. "
                "Use Nodes → Node roles to switch role and restart when required."
            ),
            "iframe_url": "/admin/nodes/node/",
            "order": 1,
            "is_active": True,
        },
    )


def unseed_operator_journey(apps, schema_editor):
    OperatorJourney = apps.get_model("ops", "OperatorJourney")
    OperatorJourney.objects.filter(slug="operator-node-readiness").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0005_operatorjourney_operatorjourneystep_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_operator_journey, unseed_operator_journey),
    ]
