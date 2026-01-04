import pytest
from django.urls import reverse

from apps.nodes.models import Node


@pytest.mark.django_db
def test_update_selected_progress_skips_downstream(admin_client):
    node = Node.objects.create(
        hostname="downstream-node",
        public_endpoint="downstream-node",
        current_relation=Node.Relation.DOWNSTREAM,
    )

    response = admin_client.post(
        reverse("admin:nodes_node_update_selected_progress"),
        {"node_id": node.pk},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["local"]["message"] == "Downstream Skipped"
    assert payload["remote"]["message"] == "Downstream Skipped"
