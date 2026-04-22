from django.db import migrations, models

PRODUCT_DEVELOPER_JOURNEY_SLUG = "product-developer-github-access"

STEP_DEFINITIONS = (
    {
        "slug": "setup-github-token",
        "order": 1,
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
    },
    {
        "slug": "review-issue-inbox",
        "order": 2,
        "title": "Review GitHub issue inbox",
        "instruction": (
            "Open the Arthexis GitHub issue inbox and triage newly synced issues. "
            "Confirm ownership, labels, and response expectations before moving forward."
        ),
        "help_text": (
            "Use the issue list to identify unreviewed work and keep team triage visible "
            "inside the suite."
        ),
        "iframe_url": "/admin/repos/repositoryissue/?state__exact=open",
    },
    {
        "slug": "review-pr-queue",
        "order": 3,
        "title": "Review pull request queue",
        "instruction": (
            "Check the pull request supervision queue to confirm incoming changes have "
            "active review coverage and no stalled approvals."
        ),
        "help_text": (
            "Use this queue to keep review throughput healthy and to spot drafts or "
            "blocked PRs quickly."
        ),
        "iframe_url": "/admin/repos/repositorypullrequest/?state__exact=open",
    },
    {
        "slug": "run-issue-lifecycle-actions",
        "order": 4,
        "title": "Execute issue lifecycle actions",
        "instruction": (
            "From the issue supervision screen, perform lifecycle actions such as adding "
            "labels, posting follow-up responses, and closing resolved issues."
        ),
        "help_text": (
            "Finish this step after confirming issue states in Arthexis reflect current "
            "delivery and support status."
        ),
        "iframe_url": "/admin/repos/repositoryissue/",
    },
    {
        "slug": "run-pr-lifecycle-actions",
        "order": 5,
        "title": "Execute pull request lifecycle actions",
        "instruction": (
            "Use the pull request supervision screen to apply lifecycle actions, including "
            "updating review status, recording outcomes, and closing completed PRs."
        ),
        "help_text": (
            "Complete this step when PR states in Arthexis accurately match merge, close, "
            "or follow-up decisions."
        ),
        "iframe_url": "/admin/repos/repositorypullrequest/",
    },
)


def seed_product_developer_github_supervision_steps(apps, schema_editor):
    """Ensure Product Developer GitHub supervision journey steps are seeded and ordered."""

    OperatorJourney = apps.get_model("ops", "OperatorJourney")
    OperatorJourneyStep = apps.get_model("ops", "OperatorJourneyStep")

    journey = OperatorJourney.objects.filter(slug=PRODUCT_DEVELOPER_JOURNEY_SLUG).first()
    if journey is None:
        return

    OperatorJourneyStep.objects.filter(journey=journey).update(
        order=models.F("order") + 1000
    )

    seeded_slugs = {definition["slug"] for definition in STEP_DEFINITIONS}
    steps_by_slug = {
        step.slug: step
        for step in OperatorJourneyStep.objects.filter(
            journey=journey,
            slug__in=seeded_slugs,
        )
    }

    for definition in STEP_DEFINITIONS:
        step = steps_by_slug.get(definition["slug"])
        if step is None:
            step = OperatorJourneyStep.objects.create(
                journey=journey,
                slug=definition["slug"],
                title=definition["title"],
                instruction=definition["instruction"],
                help_text=definition["help_text"],
                iframe_url=definition["iframe_url"],
                order=definition["order"],
                is_active=True,
                is_seed_data=True,
            )
            steps_by_slug[definition["slug"]] = step
            continue

        step.title = definition["title"]
        step.instruction = definition["instruction"]
        step.help_text = definition["help_text"]
        step.iframe_url = definition["iframe_url"]
        step.is_active = True
        step.is_seed_data = True
        step.save(
            update_fields=[
                "title",
                "instruction",
                "help_text",
                "iframe_url",
                "is_active",
                "is_seed_data",
            ]
        )

    ordered_steps = [steps_by_slug[definition["slug"]] for definition in STEP_DEFINITIONS]
    ordered_step_ids = {step.id for step in ordered_steps}
    remaining_steps = list(
        OperatorJourneyStep.objects.filter(journey=journey)
        .exclude(id__in=ordered_step_ids)
        .order_by("order", "id")
    )

    for position, step in enumerate(ordered_steps + remaining_steps, start=1):
        step.order = position
        step.save(update_fields=["order"])


def noop_reverse(apps, schema_editor):
    """Keep operator journey changes in place on reverse migration."""


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0010_split_github_setup_into_product_developer_journey"),
    ]

    operations = [
        migrations.RunPython(
            seed_product_developer_github_supervision_steps,
            noop_reverse,
        ),
    ]
