from __future__ import annotations

import uuid

import apps.core.entity
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("nodes", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ContentClassifier",
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
                        ("label", models.CharField(max_length=150)),
                        (
                            "kind",
                            models.CharField(
                                choices=[("TEXT", "Text"), ("IMAGE", "Image"), ("AUDIO", "Audio")],
                                max_length=10,
                            ),
                        ),
                        (
                            "entrypoint",
                            models.CharField(
                                help_text="Dotted path to classifier callable", max_length=255
                            ),
                        ),
                        ("run_by_default", models.BooleanField(default=True)),
                        ("active", models.BooleanField(default=True)),
                    ],
                    options={
                        "verbose_name": "Content Classifier",
                        "verbose_name_plural": "Content Classifiers",
                        "ordering": ["label"],
                        "db_table": "nodes_contentclassifier",
                    },
                    bases=(apps.core.entity.Entity,),
                ),
                migrations.CreateModel(
                    name="ContentSample",
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
                        (
                            "name",
                            models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                        ),
                        (
                            "kind",
                            models.CharField(
                                choices=[("TEXT", "Text"), ("IMAGE", "Image"), ("AUDIO", "Audio")],
                                max_length=10,
                            ),
                        ),
                        ("content", models.TextField(blank=True)),
                        ("path", models.CharField(blank=True, max_length=255)),
                        ("method", models.CharField(blank=True, default="", max_length=10)),
                        ("hash", models.CharField(blank=True, max_length=64)),
                        (
                            "transaction_uuid",
                            models.UUIDField(
                                db_index=True,
                                default=uuid.uuid4,
                                verbose_name="transaction UUID",
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "node",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                to="nodes.node",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Content Sample",
                        "verbose_name_plural": "Content Samples",
                        "ordering": ["-created_at"],
                        "db_table": "nodes_contentsample",
                    },
                    bases=(apps.core.entity.Entity,),
                ),
                migrations.CreateModel(
                    name="ContentTag",
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
                        ("label", models.CharField(max_length=150)),
                    ],
                    options={
                        "verbose_name": "Content Tag",
                        "verbose_name_plural": "Content Tags",
                        "ordering": ["label"],
                        "db_table": "nodes_contenttag",
                    },
                    bases=(apps.core.entity.Entity,),
                ),
                migrations.CreateModel(
                    name="ContentClassification",
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
                        ("confidence", models.FloatField(blank=True, null=True)),
                        ("metadata", models.JSONField(blank=True, null=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "classifier",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="classifications",
                                to="content.contentclassifier",
                            ),
                        ),
                        (
                            "sample",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="classifications",
                                to="content.contentsample",
                            ),
                        ),
                        (
                            "tag",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="classifications",
                                to="content.contenttag",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Content Classification",
                        "verbose_name_plural": "Content Classifications",
                        "ordering": ["-created_at"],
                        "unique_together": {("sample", "classifier", "tag")},
                        "db_table": "nodes_contentclassification",
                    },
                    bases=(apps.core.entity.Entity,),
                ),
                migrations.AddConstraint(
                    model_name="contentsample",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(("hash", ""), _negated=True),
                        fields=("hash",),
                        name="nodes_contentsample_hash_unique",
                    ),
                ),
            ],
            database_operations=[],
        )
    ]
