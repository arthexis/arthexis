import datetime

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.bluetooth import services
from apps.bluetooth.models import BluetoothDevice
from apps.bluetooth.services import BluetoothCommandError


@pytest.mark.django_db
def test_discover_and_sync_devices_creates_records(monkeypatch):
    fixed_now = timezone.make_aware(datetime.datetime(2024, 1, 1, 0, 0, 0))

    calls = []

    def fake_run(args):
        calls.append(tuple(args))
        args = list(args)
        if args[:2] == ["show", "hci0"]:
            return "Controller AA:BB:CC:DD:EE:FF test\n\tPowered: yes\n\tDiscoverable: no\n\tPairable: yes\n\tAlias: Test Adapter\n"
        if args[:2] == ["scan", "on"]:
            return ""
        if args[:2] == ["scan", "off"]:
            return ""
        if args[:1] == ["devices"]:
            return "Device 00:11:22:33:44:55 SensorTag\n"
        if args[:2] == ["info", "00:11:22:33:44:55"]:
            return "Name: SensorTag\nAlias: Sensor Tag\nPaired: yes\nTrusted: no\nBlocked: no\nConnected: yes\nRSSI: -40\nUUID: Battery Service\n"
        if args[:2] == ["select", "hci0"]:
            return ""
        if args[:2] == ["power", "on"]:
            return ""
        raise AssertionError(f"Unexpected bluetoothctl args: {args}")

    monkeypatch.setattr(services, "_run_bluetoothctl", fake_run)
    monkeypatch.setattr(services.timezone, "now", lambda: fixed_now)
    monkeypatch.setattr(services.time, "sleep", lambda _: None)

    result = services.discover_and_sync_devices(timeout_s=0)

    assert result == {"created": 1, "updated": 0, "count": 1}
    device = BluetoothDevice.objects.get(address="00:11:22:33:44:55")
    assert device.name == "SensorTag"
    assert device.connected is True
    assert device.rssi == -40
    assert device.uuids == ["Battery Service"]
    assert ("scan", "on") in calls
    assert ("scan", "off") in calls


@pytest.mark.django_db
def test_discover_and_sync_devices_is_idempotent(monkeypatch):
    first_now = timezone.make_aware(datetime.datetime(2024, 1, 1, 0, 0, 0))
    second_now = timezone.make_aware(datetime.datetime(2024, 1, 1, 0, 0, 2))
    now_values = iter(
        [first_now, first_now, first_now, second_now, second_now, second_now]
    )

    def fake_now():
        return next(now_values)

    def fake_run(args):
        args = list(args)
        if args[:2] == ["show", "hci0"]:
            return "Controller AA:BB:CC:DD:EE:FF test\n\tPowered: yes\n\tDiscoverable: no\n\tPairable: yes\n\tAlias: Test Adapter\n"
        if args[:2] in (
            ["scan", "on"],
            ["scan", "off"],
            ["select", "hci0"],
            ["power", "on"],
        ):
            return ""
        if args[:1] == ["devices"]:
            return "Device 00:11:22:33:44:55 SensorTag\n"
        if args[:2] == ["info", "00:11:22:33:44:55"]:
            return "Name: SensorTag\nAlias: Sensor Tag\nPaired: yes\nTrusted: no\nBlocked: no\nConnected: yes\nRSSI: -35\nUUID: Battery Service\n"
        raise AssertionError(f"Unexpected bluetoothctl args: {args}")

    monkeypatch.setattr(services, "_run_bluetoothctl", fake_run)
    monkeypatch.setattr(services.timezone, "now", fake_now)
    monkeypatch.setattr(services.time, "sleep", lambda _: None)

    first = services.discover_and_sync_devices(timeout_s=0)
    device_first = BluetoothDevice.objects.get(address="00:11:22:33:44:55")
    first_seen = device_first.first_seen_at

    second = services.discover_and_sync_devices(timeout_s=0)
    device_second = BluetoothDevice.objects.get(address="00:11:22:33:44:55")

    assert first == {"created": 1, "updated": 0, "count": 1}
    assert second == {"created": 0, "updated": 1, "count": 1}
    assert device_second.first_seen_at == first_seen
    assert device_second.last_seen_at >= first_seen


@pytest.mark.django_db
def test_discover_and_sync_devices_propagates_command_error(monkeypatch):
    monkeypatch.setattr(
        services,
        "_run_bluetoothctl",
        lambda _args: (_ for _ in ()).throw(BluetoothCommandError("boom")),
    )

    with pytest.raises(BluetoothCommandError, match="boom"):
        services.discover_and_sync_devices(timeout_s=0)


@pytest.mark.django_db
def test_register_device_sets_registration_fields(monkeypatch):
    user = get_user_model().objects.create_user(username="bt-user")
    device = BluetoothDevice.objects.create(address="AA:AA:AA:AA:AA:AA")
    fixed_now = timezone.make_aware(datetime.datetime(2024, 2, 1, 0, 0, 0))
    monkeypatch.setattr(services.timezone, "now", lambda: fixed_now)

    updated = services.register_device(device.address, user=user)

    assert updated.is_registered is True
    assert updated.registered_by == user
    assert updated.registered_at == fixed_now


@pytest.mark.django_db
def test_register_device_raises_when_missing():
    with pytest.raises(BluetoothDevice.DoesNotExist):
        services.register_device("FF:FF:FF:FF:FF:FF")
