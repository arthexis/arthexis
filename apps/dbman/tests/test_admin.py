import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test.utils import override_settings

from apps.dbman.models import ManagedDatabase

pytestmark = pytest.mark.django_db


@override_settings(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": "db.sqlite3",
        }
    }
)
def test_managed_database_admin_changelist_shows_current_database(client):
    """Regression: admin changelist should always display the current Django DB."""

    user_model = get_user_model()
    admin_user = user_model.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )
    client.force_login(admin_user)

    response = client.get(reverse("admin:dbman_manageddatabase_changelist"))

    assert response.status_code == 200
    assert ManagedDatabase.objects.filter(alias="default", is_current=True).exists()
