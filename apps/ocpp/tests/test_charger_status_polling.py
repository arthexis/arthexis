"""Tests for charger status polling behavior in the status page context."""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils import timezone

from apps.groups.models import SecurityGroup
from apps.ocpp import store
from apps.ocpp.models import Charger, Transaction


@pytest.mark.django_db
def test_status_view_disables_polling_without_active_session(client):
    """Status polling should be disabled when no live session exists."""

    user = get_user_model().objects.create_user(
        username="status-no-session", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-NO-TX")

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    assert response.context["status_should_poll"] is False


@pytest.mark.django_db
def test_status_view_enables_polling_with_active_session(client):
    """Status polling should be enabled while a live transaction is active."""

    user = get_user_model().objects.create_user(
        username="status-with-session", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-WITH-TX")
    Transaction.objects.create(charger=charger, start_time=timezone.now())

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    assert response.context["status_should_poll"] is True


@pytest.mark.django_db
def test_status_view_includes_non_transaction_events(client):
    """Status view should expose notable non-transaction events for rendering."""

    user = get_user_model().objects.create_user(
        username="status-events", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-EVENTS", connector_id=2)
    identity = store.identity_key(charger.charger_id, charger.connector_id)
    store.add_log(identity, "Connected websocket")
    store.add_log(
        identity,
        'StatusNotification processed: {"connectorId": 2, "status": "Charging"}',
    )
    store.add_log(identity, "TransactionEvent received: ignored")

    response = client.get(
        reverse(
            "ocpp:charger-status-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    assert any(item["event"] == "Connected websocket" for item in events)
    assert any(
        item["event"] == "Status" and item["details"] == "Charging" for item in events
    )
    assert all(item["severity"] in {"info", "warning", "error"} for item in events)
    assert not any("TransactionEvent" in str(item["event"]) for item in events)


@pytest.mark.django_db
def test_status_view_limits_events_to_5_entries(client):
    """Event feed should only expose the latest five notable events."""

    user = get_user_model().objects.create_user(
        username="status-events-limit", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-EVENTS-LIMIT", connector_id=2)
    identity = store.identity_key(charger.charger_id, charger.connector_id)
    for index in range(12):
        store.add_log(identity, f"Connected event-{index}")

    response = client.get(
        reverse(
            "ocpp:charger-status-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    assert len(events) == 5


@pytest.mark.django_db
def test_status_view_limits_sessions_to_5_entries(client):
    """Status page should only expose the latest five sessions."""

    user = get_user_model().objects.create_user(
        username="status-sessions-limit", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-SESSIONS-LIMIT")
    for _ in range(7):
        Transaction.objects.create(charger=charger, start_time=timezone.now())

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    assert len(response.context["transactions"]) == 5


@pytest.mark.django_db
def test_status_view_aggregate_includes_events_from_all_connectors(client):
    """Regression: aggregate status view includes notable events from all connectors."""

    user = get_user_model().objects.create_user(
        username="status-events-all-connectors", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-ALL-CONNECTORS")
    connector_a = Charger.objects.create(charger_id=charger.charger_id, connector_id=1)
    connector_b = Charger.objects.create(charger_id=charger.charger_id, connector_id=2)
    store.add_log(
        store.identity_key(charger.charger_id, connector_a.connector_id),
        "Connected connector-a",
    )
    store.add_log(
        store.identity_key(charger.charger_id, connector_b.connector_id),
        "Connected connector-b",
    )

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    names = {item["event"] for item in events}
    assert "Connected connector-a" in names
    assert "Connected connector-b" in names


@pytest.mark.django_db
def test_status_view_aggregate_deduplicates_events_from_multiple_identities(client):
    """Aggregate status view should collapse duplicate rows shared across keys."""

    user = get_user_model().objects.create_user(
        username="status-events-deduplicated", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(
        charger_id=f"STATUS-EVENTS-DEDUPE-{uuid.uuid4().hex[:8]}"
    )
    connector_a = Charger.objects.create(charger_id=charger.charger_id, connector_id=1)
    connector_b = Charger.objects.create(charger_id=charger.charger_id, connector_id=2)

    duplicate_message = (
        'StatusNotification processed: {"connectorId": 1, "status": "Preparing"}'
    )
    store.add_log(
        store.identity_key(charger.charger_id, connector_a.connector_id),
        "Connected connector-a-unique",
    )
    store.add_log(
        store.identity_key(charger.charger_id, connector_b.connector_id),
        "Connected connector-b-unique",
    )
    store.add_log(
        store.identity_key(charger.charger_id, None),
        duplicate_message,
        log_type="charger",
    )
    store.add_log(
        store.identity_key(charger.charger_id, connector_a.connector_id),
        duplicate_message,
        log_type="charger",
    )

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    deduped_status_rows = [
        row
        for row in events
        if row["event"] == "Status" and row["details"] == "Preparing"
    ]
    assert len(deduped_status_rows) == 1
    event_names = {row["event"] for row in events}
    assert "Connected connector-a-unique" in event_names
    assert "Connected connector-b-unique" in event_names


@pytest.mark.django_db
def test_status_view_aggregate_keeps_distinct_connector_status_rows(client):
    """Aggregate status view should preserve connector-specific status rows."""

    user = get_user_model().objects.create_user(
        username="status-events-by-connector", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(
        charger_id=f"STATUS-EVENTS-BY-CONNECTOR-{uuid.uuid4().hex[:8]}"
    )
    connector_a = Charger.objects.create(charger_id=charger.charger_id, connector_id=1)
    connector_b = Charger.objects.create(charger_id=charger.charger_id, connector_id=2)

    connector_a_message = (
        'StatusNotification processed: {"connectorId": 1, "status": "Available"}'
    )
    connector_b_message = (
        'StatusNotification processed: {"connectorId": 2, "status": "Available"}'
    )
    store.add_log(
        store.identity_key(charger.charger_id, connector_a.connector_id),
        connector_a_message,
        log_type="charger",
    )
    store.add_log(
        store.identity_key(charger.charger_id, connector_b.connector_id),
        connector_b_message,
        log_type="charger",
    )

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    connector_status_rows = [
        row
        for row in events
        if row["event"] == "Status" and row["details"] == "Available"
    ]
    assert len(connector_status_rows) == 2


@pytest.mark.django_db
def test_status_view_aggregate_includes_pending_events(client):
    """Regression: aggregate status view includes notable pending-key events."""

    user = get_user_model().objects.create_user(
        username="status-events-pending", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-ALL-PENDING")
    Charger.objects.create(charger_id=charger.charger_id, connector_id=1)
    store.add_log(store.pending_key(charger.charger_id), "Connected: pending")

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    assert any(item["details"] == "pending" for item in events)


@pytest.mark.django_db
def test_status_view_disables_event_admin_links_when_admin_urls_missing(
    client, monkeypatch
):
    """Event rows should render without admin links when admin URLs are unavailable."""

    user = get_user_model().objects.create_user(
        username="status-events-no-admin", password="pass", is_staff=True
    )
    client.force_login(user)
    charger = Charger.objects.create(
        charger_id="STATUS-EVENTS-NO-ADMIN", connector_id=1
    )
    identity = store.identity_key(charger.charger_id, charger.connector_id)
    store.add_log(
        identity,
        'StatusNotification processed: {"connectorId": 1, "status": "Charging", "transactionId": 1234}',
    )
    from apps.ocpp.views import public

    original_reverse = public.reverse

    def _reverse_without_admin(viewname, *args, **kwargs):
        if viewname.startswith("admin:"):
            raise NoReverseMatch("admin disabled")
        return original_reverse(viewname, *args, **kwargs)

    monkeypatch.setattr(public, "reverse", _reverse_without_admin)

    response = client.get(
        reverse(
            "ocpp:charger-status-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    assert response.context["transactions_admin_url"] is None
    assert response.context["can_view_transaction_links"] is False
    html = response.content.decode()
    assert "1234" in html
    assert "admin/ocpp/transaction/1234/change/" not in html


@pytest.mark.django_db
def test_status_view_filters_sensitive_non_transaction_events_for_non_privileged_users(
    client,
):
    """Non-privileged viewers should keep benign events while hiding sensitive logs."""

    user = get_user_model().objects.create_user(
        username="status-events-non-staff", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(
        charger_id="STATUS-EVENTS-NON-STAFF", connector_id=1
    )
    identity = store.identity_key(charger.charger_id, charger.connector_id)
    store.add_log(identity, "Connected websocket")
    store.add_log(
        identity,
        "DiagnosticsStatusNotification: status=Uploaded, location=https://diag.example/upload?token=%2A%2A%2AREDACTED%2A%2A%2A",
    )

    response = client.get(
        reverse(
            "ocpp:charger-status-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    assert any(item["event"] == "Connected websocket" for item in events)
    assert not any(item["event"] == "DiagnosticsStatusNotification" for item in events)
    html = response.content.decode()
    assert "diag.example" not in html


@pytest.mark.django_db
def test_status_view_shows_sensitive_non_transaction_events_for_owner_group_members(
    client,
):
    """Owner-group users should keep access to sensitive non-transaction events."""

    user = get_user_model().objects.create_user(
        username="status-events-owner-group", password="pass"
    )
    security_group = SecurityGroup.objects.create(name="Diagnostics Viewers")
    user.groups.add(security_group)
    client.force_login(user)
    charger = Charger.objects.create(
        charger_id="STATUS-EVENTS-OWNER-GROUP", connector_id=1
    )
    charger.owner_groups.add(security_group)
    identity = store.identity_key(charger.charger_id, charger.connector_id)
    store.add_log(
        identity,
        "DiagnosticsStatusNotification: status=Uploaded, location=https://diag.example/upload?token=%2A%2A%2AREDACTED%2A%2A%2A",
    )

    response = client.get(
        reverse(
            "ocpp:charger-status-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    assert any(
        item["event"] == "DiagnosticsStatusNotification"
        for item in response.context["non_transaction_events"]
    )


@pytest.mark.django_db
def test_status_view_shows_non_transaction_events_for_staff(client):
    """Staff users should keep access to non-transaction events in status view."""

    user = get_user_model().objects.create_user(
        username="status-events-staff", password="pass", is_staff=True
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-EVENTS-STAFF", connector_id=1)
    identity = store.identity_key(charger.charger_id, charger.connector_id)
    store.add_log(identity, "Connected websocket")
    store.add_log(
        identity,
        "DiagnosticsStatusNotification: status=Uploaded, location=https://diag.example/upload?token=%2A%2A%2AREDACTED%2A%2A%2A",
    )

    response = client.get(
        reverse(
            "ocpp:charger-status-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    assert any(
        item["event"] == "Connected websocket"
        for item in response.context["non_transaction_events"]
    )
    assert any(
        item["event"] == "DiagnosticsStatusNotification"
        for item in response.context["non_transaction_events"]
    )


@pytest.mark.django_db
def test_status_view_legacy_status_path_is_available(client):
    """Regression: legacy charger status path should render instead of 404."""

    user = get_user_model().objects.create_user(
        username="status-legacy-path", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-LEGACY-PATH")

    response = client.get(
        reverse("ocpp:charger-status-legacy", args=[charger.charger_id])
    )

    assert response.status_code == 200
