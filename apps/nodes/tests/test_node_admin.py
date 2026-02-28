from django.urls import reverse
from django.contrib.admin.sites import site

import pytest

from apps.nodes.admin import NodeAdmin
from apps.nodes.models import Node

pytestmark = pytest.mark.critical

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


@pytest.mark.django_db
def test_relation_column_displays_icon_for_each_relation():
    """The relation changelist column includes a relation-specific icon."""

    model_admin = site._registry[Node]
    icon_expectations = NodeAdmin.RELATION_ICONS

    for relation, icon in icon_expectations.items():
        node = Node(hostname=f"node-{relation}", current_relation=relation)
        html = str(model_admin.relation(node))
        assert icon in html
        assert node.get_current_relation_display() in html
