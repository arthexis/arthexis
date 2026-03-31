from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.aws.models import AWSCredentials, LightsailInstance
from apps.deploy.models import DeployInstance, DeployRun, DeployServer


pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_deploy_server_lightsail_setup_view_renders(admin_client):
    response = admin_client.get(reverse("admin:deploy_deployserver_lightsail_setup"))

    assert response.status_code == 200
    assert "Lightsail Setup Wizard" in response.content.decode()


def test_deploy_server_lightsail_setup_view_registers_existing_instance(admin_client, monkeypatch):
    credentials = AWSCredentials.objects.create(
        name="primary",
        access_key_id="AKIA_TEST",
        secret_access_key="secret",
    )

    monkeypatch.setattr(
        "apps.deploy.admin.fetch_lightsail_instance",
        lambda **kwargs: {
            "name": kwargs["name"],
            "publicIpAddress": "18.1.2.3",
            "privateIpAddress": "10.0.0.5",
            "location": {"availabilityZone": "us-east-1a"},
            "state": {"name": "running"},
            "blueprintId": "debian_12",
            "bundleId": "small_3_0",
            "arn": "arn:aws:lightsail:::instance/porsche-abb-1",
        },
    )

    response = admin_client.post(
        reverse("admin:deploy_deployserver_lightsail_setup"),
        {
            "name": "porsche-abb-1",
            "region": "us-east-1",
            "credentials": str(credentials.pk),
            "credential_label": "",
            "access_key_id": "",
            "secret_access_key": "",
            "skip_create": "on",
            "blueprint_id": "",
            "bundle_id": "",
            "key_pair_name": "",
            "availability_zone": "",
            "deploy_instance_name": "main",
            "install_dir": "/srv/porsche-abb-1",
            "service_name": "arthexis-porsche-abb-1",
            "branch": "main",
            "ocpp_port": "9000",
            "ssh_user": "ubuntu",
            "ssh_port": "22",
            "admin_url": "",
            "env_file": "",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert response.request["PATH_INFO"] == reverse("admin:deploy_deployserver_changelist")
    server = DeployServer.objects.get(name="porsche-abb-1")
    assert server.provider == DeployServer.Provider.AWS_LIGHTSAIL
    assert server.host == "18.1.2.3"
    assert LightsailInstance.objects.filter(name="porsche-abb-1", region="us-east-1").exists()
    assert DeployInstance.objects.filter(server=server, name="main").exists()
    assert DeployRun.objects.filter(instance__server=server, requested_by="lightsail_admin_wizard").exists()


def test_deploy_server_lightsail_setup_view_rejects_inline_credentials_without_aws_permission(
    client, django_user_model
):
    user = django_user_model.objects.create_user(username="limited", password="pw", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="change_deployserver"))
    client.force_login(user)

    response = client.post(
        reverse("admin:deploy_deployserver_lightsail_setup"),
        {
            "name": "porsche-abb-1",
            "region": "us-east-1",
            "credentials": "",
            "credential_label": "restricted",
            "access_key_id": "AKIA_RESTRICTED",
            "secret_access_key": "secret",
            "skip_create": "on",
            "blueprint_id": "",
            "bundle_id": "",
            "key_pair_name": "",
            "availability_zone": "",
            "deploy_instance_name": "main",
            "install_dir": "/srv/porsche-abb-1",
            "service_name": "arthexis-porsche-abb-1",
            "branch": "main",
            "ocpp_port": "9000",
            "ssh_user": "ubuntu",
            "ssh_port": "22",
            "admin_url": "",
            "env_file": "",
        },
    )

    assert response.status_code == 403
    assert not AWSCredentials.objects.filter(access_key_id="AKIA_RESTRICTED").exists()
