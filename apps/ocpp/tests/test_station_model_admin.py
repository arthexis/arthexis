import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.ocpp.models import StationModel


@pytest.mark.django_db
def test_station_model_admin_changelist_filters_by_connector_type(client):
    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password",
    )
    client.force_login(user)

    StationModel.objects.create(
        vendor="ABB",
        model_family="Terra",
        model="54",
        connector_type="CCS2",
        integration_rating=4,
    )
    StationModel.objects.create(
        vendor="Delta",
        model_family="DC Wallbox",
        model="150",
        connector_type="CHAdeMO",
        integration_rating=3,
    )

    response = client.get(
        reverse("admin:ocpp_stationmodel_changelist"),
        {"connector_type": "CCS2"},
    )

    assert response.status_code == 200
    rows = list(response.context["cl"].queryset.values_list("connector_type", flat=True))
    assert rows == ["CCS2"]
