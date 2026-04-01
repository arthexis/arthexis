import json

import pytest

from apps.nodes.models import Node, NodeEnrollment, NodeRole
from apps.nodes.services.enrollment import issue_enrollment_token
from apps.ocpp.models import Charger
from apps.nodes.views import ocpp as ocpp_views


@pytest.mark.django_db
def test_network_charger_action_accepts_ocpp_control_enrollment_token(client, monkeypatch):
    manager_role = NodeRole.objects.create(name="Gateway")
    manager_node = Node.objects.create(hostname="manager-node", role=manager_role)
    charger = Charger.objects.create(charger_id="CP-1", manager_node=manager_node, allow_remote=True)

    enrollment, token = issue_enrollment_token(node=manager_node, scope="ocpp:control")
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    manager_node.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    manager_node.save(update_fields=["mesh_enrollment_state"])

    monkeypatch.setattr("apps.nodes.views.ocpp._require_local_origin", lambda _charger: True)
    monkeypatch.setitem(ocpp_views.REMOTE_ACTIONS, "noop", lambda _charger, _payload=None: (True, "sent", {}))

    response = client.post(
        "/nodes/network/chargers/action/",
        data=json.dumps({"charger_id": "CP-1", "action": "noop"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.django_db
def test_network_charger_action_rejects_insufficient_scope_token(client):
    role = NodeRole.objects.create(name="Gateway")
    node = Node.objects.create(hostname="mesh-node", role=role)

    enrollment, token = issue_enrollment_token(node=node, scope="mesh:read")
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    node.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    node.save(update_fields=["mesh_enrollment_state"])

    response = client.post(
        "/nodes/network/chargers/action/",
        data=json.dumps({"charger_id": "CP-1", "action": "noop"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "enrollment_scope_insufficient"
