from __future__ import annotations

import pytest
from django.contrib import admin
from django.urls import reverse

from apps.aws.admin import LightsailDatabaseAdmin, LightsailInstanceAdmin
from apps.aws.models import LightsailDatabase, LightsailInstance

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_lightsail_fetch_admin_routes_and_actions_match_existing_names(admin_client):
    instance_fetch_url = reverse("admin:aws_lightsailinstance_fetch")
    database_fetch_url = reverse("admin:aws_lightsaildatabase_fetch")

    instance_admin = LightsailInstanceAdmin(LightsailInstance, admin.site)
    database_admin = LightsailDatabaseAdmin(LightsailDatabase, admin.site)

    assert any(url.name == "aws_lightsailinstance_fetch" for url in instance_admin.get_urls())
    assert any(url.name == "aws_lightsaildatabase_fetch" for url in database_admin.get_urls())
    assert instance_admin._action_url() == instance_fetch_url
    assert database_admin._action_url() == database_fetch_url


def test_lightsail_fetch_admin_metadata_and_template_context(admin_client):
    instance_response = admin_client.get(reverse("admin:aws_lightsailinstance_fetch"))
    database_response = admin_client.get(reverse("admin:aws_lightsaildatabase_fetch"))

    assert instance_response.status_code == 200
    assert database_response.status_code == 200

    assert instance_response.context_data["title"] == "Fetch Lightsail Instance"
    assert database_response.context_data["title"] == "Fetch Lightsail Database"
    assert instance_response.context_data["action_url"] == reverse("admin:aws_lightsailinstance_fetch")
    assert database_response.context_data["action_url"] == reverse("admin:aws_lightsaildatabase_fetch")
    assert instance_response.context_data["changelist_url"] == reverse("admin:aws_lightsailinstance_changelist")
    assert database_response.context_data["changelist_url"] == reverse("admin:aws_lightsaildatabase_changelist")

    assert LightsailInstanceAdmin.fetch.label == "Discover"
    assert LightsailDatabaseAdmin.fetch.label == "Discover"
    assert LightsailInstanceAdmin.fetch.short_description == "Discover"
    assert LightsailDatabaseAdmin.fetch.short_description == "Discover"
