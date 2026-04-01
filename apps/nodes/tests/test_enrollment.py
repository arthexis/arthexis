import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import RequestFactory

from apps.nodes.admin.actions import (
    approve_mesh_enrollment,
    reissue_mesh_enrollment_token,
    revoke_mesh_enrollment,
)
from apps.nodes.models import Node, NodeEnrollment, NodeEnrollmentEvent
from apps.nodes.services.enrollment import issue_enrollment_token
from apps.nodes.views.registration.handlers import submit_enrollment_public_key


@pytest.mark.django_db
def test_submit_enrollment_public_key_accepts_valid_token():
    site = Site.objects.create(domain="mesh.example.com", name="Mesh")
    node = Node.objects.create(
        hostname="node-a",
        mac_address="aa:bb:cc:dd:ee:66",
        address="198.51.100.66",
        port=8888,
        public_endpoint="node-a",
        base_site=site,
    )
    _, token = issue_enrollment_token(node=node, site=site)

    payload = {
        "mac_address": node.mac_address,
        "public_key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCtest",
        "enrollment_token": token,
        "base_site_domain": site.domain,
    }
    request = RequestFactory().post(
        "/nodes/register/enrollment-public-key/",
        data=json.dumps(payload),
        content_type="application/json",
    )

    response = submit_enrollment_public_key(request)

    node.refresh_from_db()
    enrollment = NodeEnrollment.objects.get(node=node)
    assert response.status_code == 200
    assert node.mesh_enrollment_state == Node.MeshEnrollmentState.PENDING
    assert enrollment.status == NodeEnrollment.Status.PUBLIC_KEY_SUBMITTED


class _DummyAdmin:
    def __init__(self):
        self.messages = []

    def message_user(self, request, message, level):
        self.messages.append((message, level))


@pytest.mark.django_db
def test_admin_actions_emit_enrollment_transitions():
    user = get_user_model().objects.create_superuser(
        username="mesh-admin",
        email="mesh-admin@example.com",
        password="password",
    )
    node = Node.objects.create(
        hostname="node-b",
        mac_address="aa:bb:cc:dd:ee:67",
        address="198.51.100.67",
        port=8888,
        public_endpoint="node-b",
    )
    admin = _DummyAdmin()
    request = RequestFactory().post("/admin/")
    request.user = user
    queryset = Node.objects.filter(pk=node.pk)

    reissue_mesh_enrollment_token(admin, request, queryset)
    approve_mesh_enrollment(admin, request, queryset)
    revoke_mesh_enrollment(admin, request, queryset)

    actions = list(
        NodeEnrollmentEvent.objects.filter(node=node).values_list("action", flat=True)
    )
    assert NodeEnrollmentEvent.Action.TOKEN_REISSUED in actions
    assert NodeEnrollmentEvent.Action.APPROVED in actions
    assert NodeEnrollmentEvent.Action.REVOKED in actions
