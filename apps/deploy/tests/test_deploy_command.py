from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.aws.models import AWSCredentials
from apps.deploy.models import DeployInstance, DeployRun, DeployServer

pytestmark = pytest.mark.django_db


def test_deploy_command_reports_empty_state(capsys):
    call_command("deploy")

    output = capsys.readouterr().out

    assert "No deployment instances configured yet." in output


def test_deploy_command_lists_instances_and_recent_runs(capsys):
    server = DeployServer.objects.create(name="ops-1", host="10.2.3.4")
    instance = DeployInstance.objects.create(
        server=server,
        name="main",
        install_dir="/srv/arthexis-main",
        service_name="arthexis-main",
    )
    DeployRun.objects.create(
        instance=instance,
        action=DeployRun.Action.DEPLOY,
        status=DeployRun.Status.SUCCEEDED,
    )

    call_command("deploy", "--limit", "1")

    output = capsys.readouterr().out

    assert "Configured deployment instances:" in output
    assert "ops-1:main" in output
    assert "Recent deploy runs (latest 1):" in output
    assert "action=deploy status=succeeded" in output


def test_deploy_setup_lightsail_creates_records(monkeypatch, capsys):
    credentials = AWSCredentials.objects.create(
        name="primary",
        access_key_id="AKIA_TEST",
        secret_access_key="secret",
    )

    def fake_create_lightsail_instance(**kwargs):
        return {
            "name": kwargs["name"],
            "publicIpAddress": "18.1.2.3",
            "privateIpAddress": "10.0.0.5",
        }

    def fake_fetch_lightsail_instance(**kwargs):
        return {
            "name": kwargs["name"],
            "publicIpAddress": "18.1.2.3",
            "privateIpAddress": "10.0.0.5",
            "location": {"availabilityZone": "us-east-1a"},
            "state": {"name": "running"},
            "blueprintId": "debian_12",
            "bundleId": "small_3_0",
            "arn": "arn:aws:lightsail:::instance/ops-node-1",
        }

    monkeypatch.setattr(
        "apps.deploy.management.commands.deploy.create_lightsail_instance",
        fake_create_lightsail_instance,
    )
    monkeypatch.setattr(
        "apps.deploy.management.commands.deploy.fetch_lightsail_instance",
        fake_fetch_lightsail_instance,
    )

    call_command(
        "deploy",
        "setup-lightsail",
        "--credentials",
        str(credentials.pk),
        "--region",
        "us-east-1",
        "--instance-name",
        "ops-node-1",
        "--blueprint-id",
        "debian_12",
        "--bundle-id",
        "small_3_0",
    )

    output = capsys.readouterr().out

    server = DeployServer.objects.get(name="ops-node-1")
    deploy_instance = DeployInstance.objects.get(server=server, name="main")

    assert "Lightsail deployment records configured." in output
    assert server.host == "18.1.2.3"
    assert deploy_instance.service_name == "arthexis-ops-node-1"
    assert DeployRun.objects.filter(instance=deploy_instance, action=DeployRun.Action.DEPLOY).exists()
