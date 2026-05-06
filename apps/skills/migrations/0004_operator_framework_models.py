import django.db.models.deletion
from django.db import migrations, models


def preserve_renamed_model_permissions(apps, schema_editor):
    del schema_editor
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    renamed_models = (
        ("agentskill", "skill", "Skill"),
        ("agentskillfile", "skillfile", "Skill File"),
    )
    actions = ("add", "change", "delete", "view")
    for old_model, new_model, verbose_name in renamed_models:
        old_content_type = ContentType.objects.filter(
            app_label="skills",
            model=old_model,
        ).first()
        if old_content_type is None:
            continue
        if (
            ContentType.objects.filter(app_label="skills", model=new_model)
            .exclude(pk=old_content_type.pk)
            .exists()
        ):
            continue
        for action in actions:
            Permission.objects.filter(
                content_type=old_content_type,
                codename=f"{action}_{old_model}",
            ).update(
                codename=f"{action}_{new_model}",
                name=f"Can {action} {verbose_name}",
            )
        old_content_type.model = new_model
        old_content_type.save(update_fields=["model"])


def clear_previous_seed_framework_records(apps, schema_editor):
    del schema_editor
    Skill = apps.get_model("skills", "Skill")
    Agent = apps.get_model("skills", "Agent")
    Hook = apps.get_model("skills", "Hook")
    Skill.objects.filter(is_seed_data=True).delete()
    Agent.objects.filter(is_seed_data=True).delete()
    Hook.objects.filter(is_seed_data=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("features", "0007_shorten_feature_slugs"),
        ("nodes", "0012_upgrade_policy_custom_controls"),
        ("souls", "0003_cardsession_souls_one_active_session"),
        ("skills", "0003_agentskillfile"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="AgentSkill",
            new_name="Skill",
        ),
        migrations.RenameModel(
            old_name="AgentSkillFile",
            new_name="SkillFile",
        ),
        migrations.RunPython(
            preserve_renamed_model_permissions,
            migrations.RunPython.noop,
        ),
        migrations.AlterModelOptions(
            name="skill",
            options={
                "ordering": ("slug",),
                "verbose_name": "Skill",
                "verbose_name_plural": "Skills",
            },
        ),
        migrations.AlterModelOptions(
            name="skillfile",
            options={
                "ordering": ("skill__slug", "relative_path"),
                "verbose_name": "Skill File",
                "verbose_name_plural": "Skill Files",
            },
        ),
        migrations.AddField(
            model_name="skill",
            name="description",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Compact index text for matching, RFID cards, and remote lookup.",
                max_length=720,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="skill",
            name="node_roles",
            field=models.ManyToManyField(
                blank=True,
                help_text="Optional node roles where this skill is especially relevant.",
                related_name="skills",
                to="nodes.noderole",
            ),
        ),
        migrations.AlterField(
            model_name="skillfile",
            name="skill",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="package_files",
                to="skills.skill",
            ),
        ),
        migrations.CreateModel(
            name="Agent",
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
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("title", models.CharField(max_length=150)),
                (
                    "description",
                    models.CharField(
                        blank=True,
                        help_text="Compact summary for search, cards, and package previews.",
                        max_length=720,
                    ),
                ),
                (
                    "instructions",
                    models.TextField(
                        blank=True,
                        help_text="Markdown context rendered into the local dynamic AGENTS file.",
                    ),
                ),
                (
                    "priority",
                    models.PositiveSmallIntegerField(
                        default=100,
                        help_text="Lower values render earlier within the same context tier.",
                    ),
                ),
                (
                    "is_default",
                    models.BooleanField(
                        default=False,
                        help_text="Render for every node when enabled.",
                    ),
                ),
                (
                    "node_features",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Render when the local node has one of these node features.",
                        related_name="agents",
                        to="nodes.nodefeature",
                    ),
                ),
                (
                    "node_roles",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Node role context. Role-matched rules render with highest priority.",
                        related_name="agents",
                        to="nodes.noderole",
                    ),
                ),
                (
                    "suite_features",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Render when one of these suite features is enabled for the node.",
                        related_name="agents",
                        to="features.feature",
                    ),
                ),
            ],
            options={
                "verbose_name": "Agent",
                "verbose_name_plural": "Agents",
                "ordering": ("priority", "slug"),
            },
        ),
        migrations.CreateModel(
            name="Hook",
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
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("title", models.CharField(max_length=150)),
                (
                    "description",
                    models.CharField(
                        blank=True,
                        help_text="Compact summary for search, cards, and package previews.",
                        max_length=720,
                    ),
                ),
                (
                    "event",
                    models.CharField(
                        choices=[
                            ("session_start", "Session start"),
                            ("before_prompt", "Before prompt"),
                            ("after_response", "After response"),
                            ("before_command", "Before command"),
                            ("after_command", "After command"),
                        ],
                        default="session_start",
                        max_length=32,
                    ),
                ),
                (
                    "platform",
                    models.CharField(
                        choices=[
                            ("any", "Any"),
                            ("linux", "Linux"),
                            ("windows", "Windows"),
                        ],
                        default="any",
                        max_length=16,
                    ),
                ),
                (
                    "command",
                    models.TextField(
                        help_text=(
                            "Deterministic command to run. Use portable suite paths "
                            "and SIGILS instead of operator-local paths."
                        )
                    ),
                ),
                ("working_directory", models.CharField(blank=True, max_length=500)),
                ("environment", models.JSONField(blank=True, default=dict)),
                ("timeout_seconds", models.PositiveSmallIntegerField(default=60)),
                ("enabled", models.BooleanField(default=True)),
                ("priority", models.PositiveSmallIntegerField(default=100)),
                (
                    "node_features",
                    models.ManyToManyField(
                        blank=True,
                        related_name="hooks",
                        to="nodes.nodefeature",
                    ),
                ),
                (
                    "node_roles",
                    models.ManyToManyField(
                        blank=True,
                        related_name="hooks",
                        to="nodes.noderole",
                    ),
                ),
                (
                    "suite_features",
                    models.ManyToManyField(
                        blank=True,
                        related_name="hooks",
                        to="features.feature",
                    ),
                ),
            ],
            options={
                "verbose_name": "Hook",
                "verbose_name_plural": "Hooks",
                "ordering": ("event", "priority", "slug"),
            },
        ),
        migrations.RunPython(
            clear_previous_seed_framework_records,
            migrations.RunPython.noop,
        ),
    ]
