"""Archive survey tables, then drop the runtime survey schema."""

from __future__ import annotations

from django.conf import settings
from django.db import migrations, models



def archive_survey_data(apps, schema_editor):
    """Copy survey rows into archive tables before the runtime schema is dropped."""

    SurveyTopic = apps.get_model("survey", "SurveyTopic")
    SurveyQuestion = apps.get_model("survey", "SurveyQuestion")
    SurveyResult = apps.get_model("survey", "SurveyResult")
    ArchivedSurveyTopic = apps.get_model("survey", "ArchivedSurveyTopic")
    ArchivedSurveyQuestion = apps.get_model("survey", "ArchivedSurveyQuestion")
    ArchivedSurveyResult = apps.get_model("survey", "ArchivedSurveyResult")

    for topic in SurveyTopic.objects.order_by("pk").iterator():
        ArchivedSurveyTopic.objects.update_or_create(
            original_id=topic.pk,
            defaults={
                "is_seed_data": topic.is_seed_data,
                "is_user_data": topic.is_user_data,
                "is_deleted": topic.is_deleted,
                "name": topic.name,
                "slug": topic.slug,
                "description": topic.description,
                "created_at": topic.created_at,
                "updated_at": topic.updated_at,
            },
        )

    for question in SurveyQuestion.objects.order_by("pk").iterator():
        ArchivedSurveyQuestion.objects.update_or_create(
            original_id=question.pk,
            defaults={
                "topic_original_id": question.topic_id,
                "is_seed_data": question.is_seed_data,
                "is_user_data": question.is_user_data,
                "is_deleted": question.is_deleted,
                "prompt": question.prompt,
                "question_type": question.question_type,
                "yes_label": question.yes_label,
                "no_label": question.no_label,
                "priority": question.priority,
                "position": question.position,
                "created_at": question.created_at,
                "updated_at": question.updated_at,
            },
        )

    for result in SurveyResult.objects.order_by("pk").iterator():
        ArchivedSurveyResult.objects.update_or_create(
            original_id=result.pk,
            defaults={
                "topic_original_id": result.topic_id,
                "user_id": result.user_id,
                "session_key": result.session_key,
                "is_seed_data": result.is_seed_data,
                "is_user_data": result.is_user_data,
                "is_deleted": result.is_deleted,
                "data": result.data,
                "created_at": result.created_at,
                "updated_at": result.updated_at,
            },
        )



def restore_survey_data(apps, schema_editor):
    """Recreate survey rows from the archive tables when the migration is reversed."""

    SurveyTopic = apps.get_model("survey", "SurveyTopic")
    SurveyQuestion = apps.get_model("survey", "SurveyQuestion")
    SurveyResult = apps.get_model("survey", "SurveyResult")
    ArchivedSurveyTopic = apps.get_model("survey", "ArchivedSurveyTopic")
    ArchivedSurveyQuestion = apps.get_model("survey", "ArchivedSurveyQuestion")
    ArchivedSurveyResult = apps.get_model("survey", "ArchivedSurveyResult")

    for topic in ArchivedSurveyTopic.objects.order_by("original_id").iterator():
        SurveyTopic.objects.update_or_create(
            pk=topic.original_id,
            defaults={
                "is_seed_data": topic.is_seed_data,
                "is_user_data": topic.is_user_data,
                "is_deleted": topic.is_deleted,
                "name": topic.name,
                "slug": topic.slug,
                "description": topic.description,
                "created_at": topic.created_at,
                "updated_at": topic.updated_at,
            },
        )

    for question in ArchivedSurveyQuestion.objects.order_by("original_id").iterator():
        SurveyQuestion.objects.update_or_create(
            pk=question.original_id,
            defaults={
                "topic_id": question.topic_original_id,
                "is_seed_data": question.is_seed_data,
                "is_user_data": question.is_user_data,
                "is_deleted": question.is_deleted,
                "prompt": question.prompt,
                "question_type": question.question_type,
                "yes_label": question.yes_label,
                "no_label": question.no_label,
                "priority": question.priority,
                "position": question.position,
                "created_at": question.created_at,
                "updated_at": question.updated_at,
            },
        )

    for result in ArchivedSurveyResult.objects.order_by("original_id").iterator():
        SurveyResult.objects.update_or_create(
            pk=result.original_id,
            defaults={
                "topic_id": result.topic_original_id,
                "user_id": result.user_id,
                "session_key": result.session_key,
                "is_seed_data": result.is_seed_data,
                "is_user_data": result.is_user_data,
                "is_deleted": result.is_deleted,
                "data": result.data,
                "created_at": result.created_at,
                "updated_at": result.updated_at,
            },
        )


class Migration(migrations.Migration):
    """Archive survey data into legacy tables before deleting the live schema."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("survey", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArchivedSurveyTopic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_id", models.BigIntegerField(unique=True)),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField()),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name", "original_id"]},
        ),
        migrations.CreateModel(
            name="ArchivedSurveyQuestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_id", models.BigIntegerField(unique=True)),
                ("topic_original_id", models.BigIntegerField()),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("prompt", models.TextField()),
                ("question_type", models.CharField(choices=[("binary", "Binary"), ("open", "Open ended")], default="binary", max_length=12)),
                ("yes_label", models.CharField(default="Yes", max_length=64)),
                ("no_label", models.CharField(default="No", max_length=64)),
                ("priority", models.IntegerField(default=0)),
                ("position", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["topic_original_id", "-priority", "position", "original_id"]},
        ),
        migrations.CreateModel(
            name="ArchivedSurveyResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_id", models.BigIntegerField(unique=True)),
                ("topic_original_id", models.BigIntegerField()),
                ("session_key", models.CharField(blank=True, default="", max_length=40)),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("data", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "original_id"]},
        ),
        migrations.RunPython(archive_survey_data, restore_survey_data),
        migrations.DeleteModel(name="SurveyQuestion"),
        migrations.DeleteModel(name="SurveyResult"),
        migrations.DeleteModel(name="SurveyTopic"),
    ]
