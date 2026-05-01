from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("skills", "0002_seed_agent_skills")]

    operations = [
        migrations.CreateModel(
            name="AgentSkillFile",
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
                ("relative_path", models.CharField(max_length=500)),
                ("content", models.TextField(blank=True)),
                ("content_sha256", models.CharField(blank=True, max_length=64)),
                (
                    "portability",
                    models.CharField(
                        choices=[
                            ("portable", "Portable"),
                            ("operator_scoped", "Operator scoped"),
                            ("device_scoped", "Device scoped"),
                            ("secret", "Secret"),
                            ("cache", "Cache"),
                            ("state", "State"),
                            ("generated_reference", "Generated reference"),
                        ],
                        default="portable",
                        max_length=32,
                    ),
                ),
                ("included_by_default", models.BooleanField(default=True)),
                ("exclusion_reason", models.CharField(blank=True, max_length=255)),
                ("size_bytes", models.PositiveIntegerField(default=0)),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="package_files",
                        to="skills.agentskill",
                    ),
                ),
            ],
            options={
                "verbose_name": "Agent Skill File",
                "verbose_name_plural": "Agent Skill Files",
                "ordering": ("skill__slug", "relative_path"),
                "unique_together": {("skill", "relative_path")},
            },
        ),
    ]
