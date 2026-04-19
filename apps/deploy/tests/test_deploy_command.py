from __future__ import annotations

import pytest
from django.contrib.sites.models import Site
from django.core.management import CommandError, call_command

from apps.aws.models import AWSCredentials
from apps.deploy.management.commands import lightsail as lightsail_command
from apps.deploy.models import DeployInstance, DeployRun, DeployServer
from apps.nodes.models import Node
from apps.sites.models import SiteProfile

pytestmark = pytest.mark.django_db

def test_lightsail_command_creates_records(monkeypatch, capsys):
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
            "location": {"availabilityZone": "us-east-1a"},
            "state": {"name": "running"},
            "blueprintId": "debian_12",
            "bundleId": "small_3_0",
            "arn": "arn:aws:lightsail:::instance/ops-node-1",
        }

    def fail_fetch_lightsail_instance(**kwargs):
        pytest.fail(
            "fetch_lightsail_instance should not be called when create returns details"
        )

    monkeypatch.setattr(
        "apps.deploy.management.commands.lightsail.create_lightsail_instance",
        fake_create_lightsail_instance,
    )
    monkeypatch.setattr(
        "apps.deploy.management.commands.lightsail.fetch_lightsail_instance",
        fail_fetch_lightsail_instance,
    )

    call_command(
        "lightsail",
        "--credentials",
        str(credentials.pk),
        "--region",
        "us-east-1",
        "--instance",
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
    assert DeployRun.objects.filter(
        instance=deploy_instance, action=DeployRun.Action.DEPLOY
    ).exists()

@pytest.mark.parametrize(
    ("details", "fetch_error", "expected_error"),
    [
        pytest.param(
            {},
            None,
            "Lightsail instance details were empty; setup cannot continue.",
            id="empty-details",
        ),
        pytest.param(
            {"name": "ops-node-1", "publicIpAddress": "", "privateIpAddress": ""},
            None,
            "Lightsail instance has no public/private IP yet; try again shortly.",
            id="missing-ip-addresses",
        ),
        pytest.param(
            None,
            "unable to fetch",
            "Unable to fetch Lightsail instance details: unable to fetch",
            id="fetch-lightsail-error",
        ),
    ],
)
def test_lightsail_command_rejects_interactive_prompt_in_non_tty_mode(monkeypatch):
    credentials = AWSCredentials.objects.create(
        name="root-account",
        access_key_id="AKIA_ROOT",
        secret_access_key="root-secret",
    )

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(
        CommandError,
        match="AWS MFA code is required, but interactive prompts are unavailable in non-interactive mode.",
    ):
        call_command(
            "lightsail",
            "--credentials",
            str(credentials.pk),
            "--region",
            "us-east-1",
            "--instance",
            "ops-node-1",
            "--skip-create",
            "--mfa-serial",
            "arn:aws:iam::123456789012:mfa/root-account-mfa-device",
        )

def test_lightsail_command_cleans_up_remote_instance_on_post_create_failure(
    monkeypatch,
):
    credentials = AWSCredentials.objects.create(
        name="primary",
        access_key_id="AKIA_TEST",
        secret_access_key="secret",
    )
    calls: list[str] = []

    def fake_create_lightsail_instance(**kwargs):
        calls.append(f"create:{kwargs['name']}")
        return {}

    def fake_fetch_lightsail_instance(**kwargs):
        calls.append(f"fetch:{kwargs['name']}")
        raise lightsail_command.LightsailFetchError("fetch failed")

    def fake_delete_lightsail_instance(**kwargs):
        calls.append(f"delete:{kwargs['name']}")

    monkeypatch.setattr(
        "apps.deploy.management.commands.lightsail.create_lightsail_instance",
        fake_create_lightsail_instance,
    )
    monkeypatch.setattr(
        "apps.deploy.management.commands.lightsail.fetch_lightsail_instance",
        fake_fetch_lightsail_instance,
    )
    monkeypatch.setattr(
        "apps.deploy.management.commands.lightsail.delete_lightsail_instance",
        fake_delete_lightsail_instance,
    )

    with pytest.raises(
        CommandError, match="Unable to fetch Lightsail instance details: fetch failed"
    ):
        call_command(
            "lightsail",
            "--credentials",
            str(credentials.pk),
            "--region",
            "us-east-1",
            "--instance",
            "ops-node-1",
            "--blueprint-id",
            "debian_12",
            "--bundle-id",
            "small_3_0",
        )

    assert calls == ["create:ops-node-1", "fetch:ops-node-1", "delete:ops-node-1"]

def test_lightsail_command_supports_legacy_flag_aliases(monkeypatch):
    credentials = AWSCredentials.objects.create(
        name="primary",
        access_key_id="AKIA_TEST",
        secret_access_key="secret",
    )

    monkeypatch.setattr(
        "apps.deploy.management.commands.lightsail.fetch_lightsail_instance",
        lambda **kwargs: {
            "name": kwargs["name"],
            "publicIpAddress": "18.1.2.3",
            "privateIpAddress": "10.0.0.5",
            "location": {"availabilityZone": "us-east-1a"},
            "state": {"name": "running"},
            "blueprintId": "debian_12",
            "bundleId": "small_3_0",
            "arn": "arn:aws:lightsail:::instance/ops-node-1",
        },
    )

    call_command(
        "lightsail",
        "--credentials",
        str(credentials.pk),
        "--region",
        "us-east-1",
        "--instance-name",
        "ops-node-1",
        "--key-pair-name",
        "legacy-keypair",
        "--deploy-instance-name",
        "main",
        "--service-name",
        "arthexis-ops-node-1",
        "--skip-create",
    )

    deploy_instance = DeployInstance.objects.get(server__name="ops-node-1", name="main")
    assert deploy_instance.service_name == "arthexis-ops-node-1"
