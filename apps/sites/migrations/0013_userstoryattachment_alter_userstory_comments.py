from django.db import migrations, models
import django.db.models.deletion
import apps.sites.models.user_story


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0012_alter_landinglead_status_alter_userstory_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userstory",
            name="comments",
            field=models.TextField(help_text="Share more about your experience."),
        ),
        migrations.CreateModel(
            name="UserStoryAttachment",
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
                (
                    "file",
                    models.FileField(upload_to=apps.sites.models.user_story.user_story_attachment_upload_to),
                ),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user_story",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="pages.userstory",
                    ),
                ),
            ],
            options={
                "verbose_name": "User Story Attachment",
                "verbose_name_plural": "User Story Attachments",
                "ordering": ["uploaded_at", "pk"],
            },
        ),
    ]
