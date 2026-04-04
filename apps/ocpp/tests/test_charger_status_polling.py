"""Tests for charger status polling behavior in the status page context."""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.groups.models import SecurityGroup
from apps.ocpp import store
from apps.ocpp.models import Charger, Transaction
from apps.ocpp.views.common import (
    EventFeedConfig,
    EventRow,
    classify_event_severity,
    dedupe_event_rows,
)


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


def test_dedupe_event_rows_keeps_newest_row_for_same_status_identity():
    """Status dedupe should keep the newest row for one identity collision."""

    older = EventRow(
        timestamp=timezone.now() - timezone.timedelta(minutes=5),
        event="Status",
        details="Preparing",
        severity="info",
        severity_color="#0d6efd",
        severity_label="Info",
        event_id=44,
    )
    newer = EventRow(
        timestamp=timezone.now(),
        event="Status",
        details="Preparing",
        severity="warning",
        severity_color="#ffc107",
        severity_label="Warning",
        event_id=44,
    )

    rows = dedupe_event_rows([(older, 1), (newer, 1)])

    assert rows == [newer]


@pytest.mark.django_db
def test_status_view_ignores_invalid_status_payload_rows(client):
    """Malformed status payload rows should be ignored while rendering keeps working."""

    user = get_user_model().objects.create_user(
        username="status-events-invalid-payload", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(
        charger_id="STATUS-INVALID-PAYLOAD",
        connector_id=1,
    )
    identity = store.identity_key(charger.charger_id, charger.connector_id)
    store.add_log(identity, "Connected websocket")
    store.add_log(identity, "StatusNotification processed: {not-json}")

    response = client.get(
        reverse(
            "ocpp:charger-status-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    events = response.context["non_transaction_events"]
    assert any(item["event"] == "Connected websocket" for item in events)
    assert not any(item["event"] == "Status" for item in events)


def test_classify_event_severity_handles_status_and_retry_edge_cases():
    """Severity classifier should consistently map status and retry edge cases."""

    config = EventFeedConfig(
        error_statuses=frozenset({"closed", "error", "offline", "rejected"}),
        excluded_prefixes=(),
        important_prefixes=(),
        status_prefix="StatusNotification processed:",
        warning_statuses=frozenset(
            {"faulted", "suspendedev", "suspendedevse", "unavailable"}
        ),
    )

    assert classify_event_severity("Status", "Faulted", config) == (
        "warning",
        "#ffc107",
        "Warning",
    )
    assert classify_event_severity("Status", "Closed", config) == (
        "error",
        "#dc3545",
        "Error",
    )
    assert classify_event_severity("Heartbeat", "retry in 10s", config) == (
        "warning",
        "#ffc107",
        "Warning",
    )


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


@pytest.mark.critical
def test_dedupe_event_rows_keeps_newest_status_for_out_of_order_retry_collisions():
    """Regression: out-of-order status retries should keep the newest connector row."""

    row_newest = EventRow(
        timestamp=timezone.now(),
        event="Status",
        details="Unavailable",
        severity="warning",
        severity_color="#ffc107",
        severity_label="Warning",
        event_id=512,
    )
    row_older = EventRow(
        timestamp=row_newest.timestamp - timezone.timedelta(minutes=3),
        event="Status",
        details="Unavailable",
        severity="warning",
        severity_color="#ffc107",
        severity_label="Warning",
        event_id=512,
    )
    retry_warning = EventRow(
        timestamp=row_newest.timestamp - timezone.timedelta(minutes=1),
        event="Heartbeat",
        details="retry in 10s",
        severity="warning",
        severity_color="#ffc107",
        severity_label="Warning",
        event_id=None,
    )

    rows = dedupe_event_rows([(row_older, 1), (retry_warning, 1), (row_newest, 1)])

    assert len(rows) == 2
    status_rows = [row for row in rows if row.event == "Status"]
    assert len(status_rows) == 1
    assert status_rows[0].timestamp == row_newest.timestamp
    assert any(row.event == "Heartbeat" and row.details == "retry in 10s" for row in rows)
