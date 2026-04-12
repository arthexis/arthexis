from django.db import migrations

OPERATOR_JOURNEY_SLUG = "operator-node-readiness"
PROVISION_STEP_SLUG = "provision-ops-superuser"
PRODUCT_DEVELOPER_GROUP_NAME = "Product Developer"
PRODUCT_DEVELOPER_JOURNEY_SLUG = "product-developer-github-access"
PRODUCT_DEVELOPER_GITHUB_STEP_SLUG = "setup-github-token"


def split_github_setup_into_product_developer_journey(apps, schema_editor):
    """Keep ops provisioning focused and move GitHub setup to product developers."""

    OperatorJourney = apps.get_model("ops", "OperatorJourney")
    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")
    SecurityGroup = apps.get_model("groups", "SecurityGroup")

    OperatorJourneyStep.objects.filter(
        journey__slug=OPERATOR_JOURNEY_SLUG,
        slug=PROVISION_STEP_SLUG,
        is_seed_data=True,
    ).update(
        title="Create operational superuser and security access",
        instruction=(
            "After confirming node role/setup, create a dedicated superuser for operations. "
            "Assign one or more security groups and choose random/manual password handling."
        ),
        help_text=(
            "Complete this step by creating the account, recording credentials securely, "
            "and verifying the account can log in."
        ),
    )

    product_developer_group, _ = SecurityGroup.objects.get_or_create(
        name=PRODUCT_DEVELOPER_GROUP_NAME
    )
    journey, created = OperatorJourney.objects.get_or_create(
        slug=PRODUCT_DEVELOPER_JOURNEY_SLUG,
        defaults={
            "name": "Product Developer GitHub Access",
            "description": "Guided GitHub token setup for product developers.",
            "security_group": product_developer_group,
            "priority": 20,
            "is_active": True,
            "is_seed_data": True,
        },
    )

    update_fields = []
    if journey.security_group_id != product_developer_group.id:
        journey.security_group = product_developer_group
        update_fields.append("security_group")
    if created:
        update_fields.append("is_seed_data")
    elif not journey.is_seed_data:
        journey.is_seed_data = True
        update_fields.append("is_seed_data")
    if update_fields:
        journey.save(update_fields=update_fields)

    step, _ = OperatorJourneyStep.objects.get_or_create(
        journey=journey,
        slug=PRODUCT_DEVELOPER_GITHUB_STEP_SLUG,
        defaults={
            "title": "Connect your GitHub access",
            "instruction": (
                "Use the GitHub token setup wizard to connect your product developer account "
                "for repository, release, and issue workflows."
            ),
            "help_text": (
                "This step is for Product Developer members only. "
                "The wizard opens your token record and keeps setup in one place."
            ),
            "iframe_url": "/admin/repos/githubrepository/setup-token/",
            "order": 1,
            "is_active": True,
            "is_seed_data": True,
        },
    )
    if not step.is_seed_data:
        step.is_seed_data = True
        step.save(update_fields=["is_seed_data"])


def noop_reverse(apps, schema_editor):
    """Keep user-updated journey configuration intact on reverse."""


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0009_seed_operator_journey_superuser_provision_step"),
    ]

    operations = [
        migrations.RunPython(
            split_github_setup_into_product_developer_journey,
            noop_reverse,
        ),
    ]
