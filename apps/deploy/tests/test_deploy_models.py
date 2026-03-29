from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.deploy.models import DeployInstance, DeployServer

pytestmark = pytest.mark.django_db


def test_deploy_instance_normalizes_paths():
    server = DeployServer.objects.create(name="lightsail-1", host="10.1.2.3")
    instance = DeployInstance(
        server=server,
        name="main",
        install_dir="/srv/arthexis/../arthexis-main",
        env_file="/etc/arthexis/../deploy/main.env",
        service_name="arthexis-main",
    )

    instance.full_clean()

    assert instance.install_dir == "/srv/arthexis-main"
    assert instance.env_file == "/etc/deploy/main.env"


def test_deploy_instance_rejects_relative_paths():
    server = DeployServer.objects.create(name="lightsail-2", host="10.1.2.4")
    instance = DeployInstance(
        server=server,
        name="main",
        install_dir="srv/arthexis-main",
        service_name="arthexis-main",
    )

    with pytest.raises(ValidationError) as exc_info:
        instance.full_clean()

    assert "install_dir" in exc_info.value.message_dict


@pytest.mark.parametrize(
    "duplicate_fields",
    [
        {"name": "instance-a"},
        {"install_dir": "/srv/arthexis-a"},
        {"service_name": "arthexis-a"},
    ],
)
def test_deploy_instance_enforces_unique_constraints_per_server(duplicate_fields):
    server = DeployServer.objects.create(name="lightsail-3", host="10.1.2.5")
    DeployInstance.objects.create(
        server=server,
        name="instance-a",
        install_dir="/srv/arthexis-a",
        service_name="arthexis-a",
    )

    with pytest.raises(IntegrityError):
        data = {
            "server": server,
            "name": "instance-b",
            "install_dir": "/srv/arthexis-b",
            "service_name": "arthexis-b",
        }
        data.update(duplicate_fields)
        DeployInstance.objects.create(**data)


def test_multiple_instances_can_share_names_across_servers():
    server_a = DeployServer.objects.create(name="lightsail-4", host="10.1.2.6")
    server_b = DeployServer.objects.create(name="lightsail-5", host="10.1.2.7")

    DeployInstance.objects.create(
        server=server_a,
        name="prod",
        install_dir="/srv/prod",
        service_name="arthexis-prod",
    )
    DeployInstance.objects.create(
        server=server_b,
        name="prod",
        install_dir="/srv/prod",
        service_name="arthexis-prod",
    )

    assert DeployInstance.objects.filter(name="prod").count() == 2


def test_deploy_instance_treats_blank_env_file_as_optional():
    server = DeployServer.objects.create(name="lightsail-6", host="10.1.2.8")
    instance = DeployInstance(
        server=server,
        name="optional-env",
        install_dir="/srv/optional-env",
        env_file="   ",
        service_name="optional-env",
    )

    instance.full_clean()

    assert instance.env_file == ""


def test_deploy_instance_save_enforces_path_validation():
    server = DeployServer.objects.create(name="lightsail-7", host="10.1.2.9")

    with pytest.raises(ValidationError) as exc_info:
        DeployInstance.objects.create(
            server=server,
            name="invalid-path",
            install_dir="relative/path",
            service_name="invalid-path",
        )

    assert "install_dir" in exc_info.value.message_dict
