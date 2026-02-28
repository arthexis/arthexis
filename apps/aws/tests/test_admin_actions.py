"""Regression tests for AWS admin load-instance actions."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.aws.models import AWSCredentials, LightsailInstance



def test_credentials_changelist_load_instances_action(client, monkeypatch, db) -> None:
    """Credentials changelist tool should load instances without a queryset."""

    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    assert client.login(username="admin", password="admin123")

    credential = AWSCredentials.objects.create(
        name="Primary",
        access_key_id="AKIA123",
        secret_access_key="secret123",
    )

    def fake_sync(**kwargs):
        obj, created = LightsailInstance.objects.update_or_create(
            name="wt-instance",
            region="us-east-1",
            defaults={"credentials": kwargs.get("credentials")},
        )
        return {
            "created": 1 if created else 0,
            "updated": 0 if created else 1,
            "instances": [obj],
            "created_ids": {obj.pk} if created else set(),
            "updated_ids": set() if created else {obj.pk},
        }

    monkeypatch.setattr("apps.aws.admin.sync_lightsail_instances", fake_sync)

    response = client.post(reverse("admin:aws_awscredentials_load_instances"))

    assert response.status_code == 302
    credential.refresh_from_db()
    assert LightsailInstance.objects.filter(credentials=credential).count() == 1



def test_credentials_selected_action_loads_only_selected(client, monkeypatch, db) -> None:
    """Select-box action should only sync instances for selected credentials."""

    user = get_user_model().objects.create_superuser(
        username="admin2",
        email="admin2@example.com",
        password="admin123",
    )
    assert client.login(username="admin2", password="admin123")

    selected = AWSCredentials.objects.create(
        name="Selected",
        access_key_id="AKIASEL",
        secret_access_key="secret1",
    )
    AWSCredentials.objects.create(
        name="Not Selected",
        access_key_id="AKIANOT",
        secret_access_key="secret2",
    )

    seen: list[str] = []

    def fake_sync(**kwargs):
        credential = kwargs["credentials"]
        seen.append(credential.name)
        return {
            "created": 0,
            "updated": 0,
            "instances": [],
            "created_ids": set(),
            "updated_ids": set(),
        }

    monkeypatch.setattr("apps.aws.admin.sync_lightsail_instances", fake_sync)

    response = client.post(
        reverse("admin:aws_awscredentials_changelist"),
        {
            "action": "load_instances_for_selected",
            "_selected_action": [str(selected.pk)],
            "index": "0",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert seen == ["Selected"]


def test_credentials_changelist_shows_load_instances_object_tool(client, db) -> None:
    """Credentials changelist should render a top object-tool Load Instances button."""

    get_user_model().objects.create_superuser(
        username="admin3",
        email="admin3@example.com",
        password="admin123",
    )
    assert client.login(username="admin3", password="admin123")

    response = client.get(reverse("admin:aws_awscredentials_changelist"))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'action="{reverse("admin:aws_awscredentials_load_instances")}"' in content
    assert "Load Instances" in content


def test_instances_changelist_shows_load_instances_object_tool(client, db) -> None:
    """Instance changelist should render a top object-tool Load Instances button."""

    get_user_model().objects.create_superuser(
        username="admin4",
        email="admin4@example.com",
        password="admin123",
    )
    assert client.login(username="admin4", password="admin123")

    response = client.get(reverse("admin:aws_lightsailinstance_changelist"))

    content = response.content.decode()
    assert response.status_code == 200
    assert f'action="{reverse("admin:aws_lightsailinstance_load_instances")}"' in content
    assert "Load Instances" in content


def test_credentials_load_instances_requires_post(client, db) -> None:
    """Direct credentials load endpoint should reject GET requests."""

    get_user_model().objects.create_superuser(
        username="admin5",
        email="admin5@example.com",
        password="admin123",
    )
    assert client.login(username="admin5", password="admin123")

    response = client.get(reverse("admin:aws_awscredentials_load_instances"))

    assert response.status_code == 405


def test_instances_load_instances_requires_post(client, db) -> None:
    """Direct instance load endpoint should reject GET requests."""

    get_user_model().objects.create_superuser(
        username="admin6",
        email="admin6@example.com",
        password="admin123",
    )
    assert client.login(username="admin6", password="admin123")

    response = client.get(reverse("admin:aws_lightsailinstance_load_instances"))

    assert response.status_code == 405
