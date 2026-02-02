import logging

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from apps.base.models import Entity


@pytest.mark.django_db(transaction=True)
@pytest.mark.critical
def test_seed_soft_delete_skipped_for_constrained_model(caplog):
    with isolate_apps("tests"):

        class ConstrainedEntity(Entity):
            name = models.CharField(max_length=64)

            class Meta:
                app_label = "tests"
                constraints = [
                    models.CheckConstraint(
                        condition=models.Q(is_deleted=False),
                        name="tests_constrainedentity_is_deleted_false",
                    )
                ]

        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(ConstrainedEntity)

        try:
            instance = ConstrainedEntity.objects.create(name="seed", is_seed_data=True)
            with caplog.at_level(logging.INFO):
                instance.delete()

            instance.refresh_from_db()
            assert instance.is_deleted is False
            assert "Skipping soft delete" in caplog.text
        finally:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(ConstrainedEntity)


@pytest.mark.django_db(transaction=True)
def test_seed_soft_delete_applies_without_constraint():
    with isolate_apps("tests"):

        class UnconstrainedEntity(Entity):
            name = models.CharField(max_length=64)

            class Meta:
                app_label = "tests"

        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(UnconstrainedEntity)

        try:
            instance = UnconstrainedEntity.objects.create(name="seed", is_seed_data=True)
            instance.delete()

            instance = UnconstrainedEntity.all_objects.get(pk=instance.pk)
            assert instance.is_deleted is True
        finally:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(UnconstrainedEntity)
