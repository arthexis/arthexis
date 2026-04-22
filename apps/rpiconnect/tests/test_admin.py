"""Admin UI regression tests for Raspberry Pi Connect operations."""

import pytest
from django.contrib import admin
from django.urls import reverse

from apps.rpiconnect.models import (
    ConnectAccount,
    ConnectDevice,
    ConnectImageRelease,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)


@pytest.mark.django_db
def test_connect_device_admin_shows_inventory_eligibility_indicators():
    """Device admin should render inventory signal columns used for eligibility checks."""

    model_admin = admin.site._registry[ConnectDevice]
    account = ConnectAccount.objects.create(
        name="Ops",
        account_type=ConnectAccount.AccountType.ORG,
        organization_name="Arthexis",
        token_reference="token",
    )
    device = ConnectDevice.objects.create(
        account=account,
        device_id="pi-01",
        hardware_model="rpi-4b",
        metadata={"connectivity": "ethernet", "free_space": "12 GB"},
        os_release="bookworm",
    )

    assert model_admin.connectivity_indicator(device) == "ethernet"
    assert model_admin.free_space_indicator(device) == "12 GB"
    assert "Eligible" in model_admin.eligibility_indicator(device)


@pytest.mark.django_db
def test_campaign_wizard_creates_campaign_and_progress_page_renders(admin_client):
    """Campaign wizard should create admin-configured campaign and expose progress drill-down."""

    account = ConnectAccount.objects.create(
        name="Ops",
        account_type=ConnectAccount.AccountType.ORG,
        organization_name="Arthexis",
        token_reference="token",
    )
    device = ConnectDevice.objects.create(
        account=account,
        device_id="pi-02",
        hardware_model="rpi-4b",
        os_release="bookworm",
    )
    release = ConnectImageRelease.objects.create(
        name="stable",
        version="1.2.3",
        artifact_url="https://cdn.example.com/stable.img",
        checksum="abc123",
        compatibility_tags=["rpi-4b", "bookworm"],
    )

    response = admin_client.post(
        reverse("admin:rpiconnect_campaign_wizard"),
        data={
            "release": release.pk,
            "device_ids": device.device_id,
            "labels": "",
            "cohorts": "",
            "strategy": ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
            "canary_percent": 10,
            "batch_size": 10,
            "launch_timing": "start_now",
            "timing_notes": "02:00 UTC",
            "notes": "Rollout",
            "override_conflicts": False,
        },
    )

    assert response.status_code == 302

    campaign = ConnectUpdateCampaign.objects.get()
    assert campaign.status == ConnectUpdateCampaign.Status.RUNNING
    assert "Timing notes: 02:00 UTC" in campaign.notes

    deployment = ConnectUpdateDeployment.objects.get(campaign=campaign)
    deployment.status = ConnectUpdateDeployment.Status.FAILED
    deployment.error_payload = {"reason": "checksum mismatch"}
    deployment.save()

    progress_response = admin_client.get(reverse("admin:rpiconnect_campaign_progress", args=[campaign.pk]))
    assert progress_response.status_code == 200
    page = progress_response.content.decode("utf-8")
    assert "Device-level failures and rollbacks" in page
    assert "checksum mismatch" in page


@pytest.mark.django_db
def test_campaign_admin_rollback_creates_previous_release_campaign(admin_client):
    """Rollback action should create a new campaign targeting succeeded devices on previous release."""

    account = ConnectAccount.objects.create(
        name="Ops",
        account_type=ConnectAccount.AccountType.ORG,
        organization_name="Arthexis",
        token_reference="token",
    )
    device = ConnectDevice.objects.create(
        account=account,
        device_id="pi-03",
        hardware_model="rpi-4b",
        os_release="bookworm",
    )
    previous_release = ConnectImageRelease.objects.create(
        name="stable",
        version="1.2.2",
        artifact_url="https://cdn.example.com/stable-1.2.2.img",
        checksum="chk-prev",
        compatibility_tags=["rpi-4b", "bookworm"],
    )
    current_release = ConnectImageRelease.objects.create(
        name="stable",
        version="1.2.3",
        artifact_url="https://cdn.example.com/stable-1.2.3.img",
        checksum="chk-cur",
        compatibility_tags=["rpi-4b", "bookworm"],
    )

    previous_campaign = ConnectUpdateCampaign.objects.create(
        release=previous_release,
        target_set={"device_ids": [device.device_id], "labels": [], "cohorts": []},
        strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
        status=ConnectUpdateCampaign.Status.COMPLETED,
    )
    ConnectUpdateDeployment.objects.create(
        campaign=previous_campaign,
        device=device,
        status=ConnectUpdateDeployment.Status.SUCCEEDED,
    )

    current_campaign = ConnectUpdateCampaign.objects.create(
        release=current_release,
        target_set={"device_ids": [device.device_id], "labels": [], "cohorts": []},
        strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
        status=ConnectUpdateCampaign.Status.RUNNING,
    )
    ConnectUpdateDeployment.objects.create(
        campaign=current_campaign,
        device=device,
        status=ConnectUpdateDeployment.Status.SUCCEEDED,
    )

    response = admin_client.post(reverse("admin:rpiconnect_campaign_rollback", args=[current_campaign.pk]))

    assert response.status_code == 302
    rollback_campaign = ConnectUpdateCampaign.objects.exclude(
        pk__in=[previous_campaign.pk, current_campaign.pk]
    ).get()
    assert rollback_campaign.release == previous_release
    assert rollback_campaign.status == ConnectUpdateCampaign.Status.RUNNING
