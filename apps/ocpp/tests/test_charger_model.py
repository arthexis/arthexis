import datetime as dt
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.groups.constants import NETWORK_OPERATOR_GROUP_NAME, SITE_OPERATOR_GROUP_NAME
from apps.ocpp.models import Charger
from apps.groups.models import SecurityGroup
from apps.nodes.models import Node


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("last_status_offset", "last_heartbeat_offset", "expected_field"),
    [(0, 5, "last_status_timestamp"), (None, 0, "last_heartbeat")],
)
def test_last_seen_uses_expected_source(last_status_offset, last_heartbeat_offset, expected_field):
    heartbeat = timezone.now() - dt.timedelta(minutes=last_heartbeat_offset)
    status_timestamp = (
        None
        if last_status_offset is None
        else timezone.now() - dt.timedelta(minutes=last_status_offset)
    )
    charger = Charger.objects.create(
        charger_id=f"CH-{expected_field}",
        last_status_timestamp=status_timestamp,
        last_heartbeat=heartbeat,
    )
    assert charger.last_seen == getattr(charger, expected_field)


def test_create_charger_ignores_stale_local_node_cache():
    mac = Node.get_current_mac()
    stale_node = Node(
        id=9999,
        hostname="stale",
        mac_address=mac,
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache[mac] = (stale_node, timezone.now() + timedelta(hours=1))

    charger = Charger.objects.create(charger_id="CH-3")

    assert charger.manager_node_id is None
    Node._local_cache.clear()


def test_charger_defaults_to_site_operator_group():
    group, _ = SecurityGroup.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)
    charger = Charger.objects.create(charger_id="CH-4")

    assert charger.group_id == group.pk


def test_offline_notification_source_prefers_station_defaults():
    station = Charger.objects.create(
        charger_id="CH-5",
        email_when_offline=True,
        maintenance_email="station@example.com",
    )
    connector = Charger.objects.create(charger_id="CH-5", connector_id=1)

    source = connector.offline_notification_source()

    assert source.pk == station.pk
    assert connector.maintenance_email_value() == "station@example.com"
    assert connector.email_when_offline_value() is True


def test_offline_notification_source_prefers_connector_settings():
    _station = Charger.objects.create(
        charger_id="CH-6",
        email_when_offline=True,
        maintenance_email="station@example.com",
    )
    connector = Charger.objects.create(
        charger_id="CH-6",
        connector_id=2,
        email_when_offline=True,
        maintenance_email="connector@example.com",
    )

    source = connector.offline_notification_source()

    assert source.pk == connector.pk
    assert connector.maintenance_email_value() == "connector@example.com"
    assert connector.email_when_offline_value() is True


def test_charge_station_manager_can_view_owner_scoped_charger(django_user_model):
    manager_group, _ = SecurityGroup.objects.get_or_create(
        name=NETWORK_OPERATOR_GROUP_NAME
    )
    manager = django_user_model.objects.create_user(
        username="charger-manager", password="secret"
    )
    manager.groups.add(manager_group)

    owner = django_user_model.objects.create_user(username="owner", password="secret")
    charger = Charger.objects.create(charger_id="CH-OWNER")
    charger.owner_users.add(owner)

    assert charger.is_visible_to(manager) is True


def test_charge_station_manager_is_authorized_for_ws_auth(django_user_model):
    manager_group, _ = SecurityGroup.objects.get_or_create(
        name=NETWORK_OPERATOR_GROUP_NAME
    )
    manager = django_user_model.objects.create_user(
        username="charger-manager-auth", password="secret"
    )
    manager.groups.add(manager_group)

    designated_user = django_user_model.objects.create_user(
        username="designated", password="secret"
    )
    charger = Charger.objects.create(
        charger_id="CH-AUTH-MANAGER",
        ws_auth_user=designated_user,
    )

    assert charger.is_ws_user_authorized(manager) is True
