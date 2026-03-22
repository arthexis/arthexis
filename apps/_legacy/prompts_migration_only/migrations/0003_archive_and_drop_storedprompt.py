"""Archive prompt records, then drop the runtime prompts schema."""

from __future__ import annotations

from django.db import migrations, models


def _preserve_model_timestamps(model, *, pk, created_at, updated_at):
    """Persist historical timestamps exactly, bypassing auto_now fields."""

    model.objects.filter(pk=pk).update(
        created_at=created_at,
        updated_at=updated_at,
    )


def _remove_prompt_application_metadata(apps):
    """Delete persisted prompts application rows so the retired app stays hidden."""

    Application = apps.get_model("app", "Application")
    ApplicationModel = apps.get_model("app", "ApplicationModel")

    prompt_applications = Application.objects.filter(name="prompts")
    ApplicationModel.objects.filter(application__in=prompt_applications).delete()
    prompt_applications.delete()


def _restore_prompt_application_metadata(apps):
    """Recreate the prompts application row when reversing decommissioning."""

    Application = apps.get_model("app", "Application")

    Application.objects.update_or_create(
        name="prompts",
        defaults={"description": ""},
    )


def archive_prompt_data(apps, schema_editor):
    """Copy stored prompt rows into an archive table before the live table is dropped."""

    StoredPrompt = apps.get_model("prompts", "StoredPrompt")
    ArchivedStoredPrompt = apps.get_model("prompts", "ArchivedStoredPrompt")

    for prompt in StoredPrompt.objects.order_by("pk").iterator():
        ArchivedStoredPrompt.objects.update_or_create(
            original_id=prompt.pk,
            defaults={
                "is_seed_data": prompt.is_seed_data,
                "is_user_data": prompt.is_user_data,
                "is_deleted": prompt.is_deleted,
                "slug": prompt.slug,
                "title": prompt.title,
                "prompt_text": prompt.prompt_text,
                "initial_plan": prompt.initial_plan,
                "change_reference": prompt.change_reference,
                "context": prompt.context,
                "created_at": prompt.created_at,
                "updated_at": prompt.updated_at,
            },
        )

    _remove_prompt_application_metadata(apps)


def restore_prompt_data(apps, schema_editor):
    """Recreate stored prompt rows from the archive table when reversing the migration."""

    StoredPrompt = apps.get_model("prompts", "StoredPrompt")
    ArchivedStoredPrompt = apps.get_model("prompts", "ArchivedStoredPrompt")

    for prompt in ArchivedStoredPrompt.objects.order_by("original_id").iterator():
        StoredPrompt.objects.update_or_create(
            pk=prompt.original_id,
            defaults={
                "is_seed_data": prompt.is_seed_data,
                "is_user_data": prompt.is_user_data,
                "is_deleted": prompt.is_deleted,
                "slug": prompt.slug,
                "title": prompt.title,
                "prompt_text": prompt.prompt_text,
                "initial_plan": prompt.initial_plan,
                "change_reference": prompt.change_reference,
                "context": prompt.context,
            },
        )
        _preserve_model_timestamps(
            StoredPrompt,
            pk=prompt.original_id,
            created_at=prompt.created_at,
            updated_at=prompt.updated_at,
        )

    _restore_prompt_application_metadata(apps)


class Migration(migrations.Migration):
    """Archive prompt rows into legacy tables before deleting the live schema."""

    dependencies = [
        ("app", "0002_applicationmodel"),
        ("prompts", "0002_rename_pr_reference_change_reference"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArchivedStoredPrompt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("original_id", models.BigIntegerField(unique=True)),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("slug", models.SlugField(max_length=120)),
                ("title", models.CharField(max_length=200)),
                ("prompt_text", models.TextField()),
                ("initial_plan", models.TextField()),
                (
                    "change_reference",
                    models.CharField(blank=True, default="", max_length=120),
                ),
                ("context", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["slug", "original_id"]},
        ),
        migrations.RunPython(archive_prompt_data, restore_prompt_data),
        migrations.DeleteModel(name="StoredPrompt"),
    ]
