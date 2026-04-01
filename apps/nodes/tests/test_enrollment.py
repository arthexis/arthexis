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
from apps.nodes.services.enrollment import approve_enrollment, issue_enrollment_token, submit_public_key
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


@pytest.mark.django_db
def test_reissue_enrollment_token_revokes_prior_active_tokens():
    node = Node.objects.create(
        hostname="node-c",
        mac_address="aa:bb:cc:dd:ee:68",
        address="198.51.100.68",
        port=8888,
        public_endpoint="node-c",
    )

    previous_enrollment, _ = issue_enrollment_token(node=node)
    latest_enrollment, _ = issue_enrollment_token(node=node, reissue=True)

    previous_enrollment.refresh_from_db()
    latest_enrollment.refresh_from_db()
    assert previous_enrollment.status == NodeEnrollment.Status.REVOKED
    assert previous_enrollment.revoked_at is not None
    assert latest_enrollment.status == NodeEnrollment.Status.ISSUED


@pytest.mark.django_db
def test_submit_enrollment_public_key_rejects_missing_site_for_scoped_token():
    site = Site.objects.create(domain="mesh-2.example.com", name="Mesh 2")
    node = Node.objects.create(
        hostname="node-d",
        mac_address="aa:bb:cc:dd:ee:69",
        address="198.51.100.69",
        port=8888,
        public_endpoint="node-d",
        base_site=site,
    )
    _, token = issue_enrollment_token(node=node, site=site)

    enrollment, error = submit_public_key(
        node=node,
        token=token,
        public_key="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCsite",
        site=None,
    )

    assert enrollment is None
    assert error == "Enrollment token does not match target site"


@pytest.mark.django_db
def test_approve_enrollment_does_not_activate_issued_without_public_key():
    node = Node.objects.create(
        hostname="node-e",
        mac_address="aa:bb:cc:dd:ee:70",
        address="198.51.100.70",
        port=8888,
        public_endpoint="node-e",
    )
    enrollment, _ = issue_enrollment_token(node=node)

    approve_enrollment(node=node)

    enrollment.refresh_from_db()
    assert enrollment.status == NodeEnrollment.Status.ISSUED


@pytest.mark.django_db
def test_issue_enrollment_token_rejects_unknown_scope():
    node = Node.objects.create(
        hostname="node-f",
        mac_address="aa:bb:cc:dd:ee:71",
        address="198.51.100.71",
        port=8888,
        public_endpoint="node-f",
    )

    with pytest.raises(ValueError, match="Unsupported enrollment scope"):
        issue_enrollment_token(node=node, scope=" mesh:write ")
