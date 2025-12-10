import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_charger_admin_changelist_accessible(client):
    User = get_user_model()
    user = User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.force_login(user)

    url = reverse("admin:ocpp_charger_changelist")
    response = client.get(url)

    assert response.status_code == 200
    assert b"Charge Point" in response.content
