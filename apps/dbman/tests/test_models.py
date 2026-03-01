import pytest
from django.test.utils import override_settings

from apps.dbman.models import ManagedDatabase

pytestmark = pytest.mark.django_db


@override_settings(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": "db.sqlite3",
            "HOST": "",
            "PORT": "",
            "USER": "",
        },
        "analytics": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "analytics",
            "HOST": "analytics.internal",
            "PORT": "5432",
            "USER": "analyst",
        },
    }
)
def test_sync_from_settings_creates_current_database_row():
    """Current default database should always be present after synchronization."""

    records = ManagedDatabase.sync_from_settings()

    aliases = {record.alias for record in records}
    assert aliases == {"analytics", "default"}

    current = ManagedDatabase.objects.get(alias="default")
    assert current.is_current is True
    assert current.is_django_connection is True


@override_settings(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": "db.sqlite3",
        }
    }
)
def test_sync_from_settings_keeps_external_databases_not_current():
    """External databases remain manageable while no longer flagged current."""

    ManagedDatabase.objects.create(
        alias="warehouse",
        display_name="warehouse",
        engine="django.db.backends.postgresql",
        is_django_connection=False,
        is_current=True,
    )

    ManagedDatabase.sync_from_settings()

    external = ManagedDatabase.objects.get(alias="warehouse")
    assert external.is_current is False
    assert external.is_django_connection is False
