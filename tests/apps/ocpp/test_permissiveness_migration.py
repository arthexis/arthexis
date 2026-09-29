import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.migration
def test_permissiveness_migration_preserves_existing_open_charger_behavior() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([("ocpp", "0026_chargertimelineprogress")])

    old_apps = executor.loader.project_state(
        [("ocpp", "0026_chargertimelineprogress")]
    ).apps
    OldCharger = old_apps.get_model("ocpp", "Charger")
    OldCharger.objects.create(
        identity="existing-open",
        authorization_mode="open",
    )

    executor = MigrationExecutor(connection)
    executor.migrate([("ocpp", "0027_ocpp_permissiveness_defaults")])

    new_apps = executor.loader.project_state(
        [("ocpp", "0027_ocpp_permissiveness_defaults")]
    ).apps
    NewCharger = new_apps.get_model("ocpp", "Charger")
    migrated = NewCharger.objects.get(identity="existing-open")

    assert migrated.authorization_mode == "open"
    assert migrated.protocol_mode == "open"
    assert NewCharger._meta.get_field("authorization_mode").default == "restricted"
    assert NewCharger._meta.get_field("protocol_mode").default == "restricted"

    operator_created = NewCharger.objects.create(identity="operator-created-after")
    assert operator_created.authorization_mode == "restricted"
    assert operator_created.protocol_mode == "restricted"
