import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.ocpp.models import Charger


pytestmark = pytest.mark.django_db


def test_charger_admin_changelist_populates_quick_stats(client):
    """Charger changelist should still include quick stats context."""
    user = get_user_model().objects.create_superuser(
        username="admin-metrics",
        password="pass",
        email="admin-metrics@example.com",
    )
    client.force_login(user)

    Charger.objects.create(charger_id="CP-ADMIN")
    Charger.objects.create(charger_id="CP-ADMIN", connector_id=1)

    response = client.get(reverse("admin:ocpp_charger_changelist"))

    assert response.status_code == 200
    context = response.context[-1]
    assert "charger_quick_stats" in context
    stats = context["charger_quick_stats"]
    assert stats["total_kw"] == 0.0
    assert stats["today_kw"] == 0.0
    assert stats["estimated_cost"] is None
    assert stats["availability_percentage"] is None
