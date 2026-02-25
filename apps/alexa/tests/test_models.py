"""Model tests for Alexa reminders and account credentials."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.alexa.models import (
    AlexaAccount,
    AlexaCredentialsError,
    AlexaReminder,
    AlexaReminderDelivery,
)


@pytest.mark.django_db
def test_alexa_account_validate_credentials_requires_all_values():
    """Regression: account credential validation should fail for missing secrets."""

    user = get_user_model().objects.create_user(username="alexa-owner")
    account = AlexaAccount.objects.create(
        name="Office Alexa",
        user=user,
        client_id="client-id",
        client_secret="",
        refresh_token="refresh-token",
    )

    with pytest.raises(AlexaCredentialsError):
        account.validate_credentials()


@pytest.mark.django_db
def test_alexa_reminder_mark_event_update_only_marks_sent_like_deliveries():
    """Regression: event updates should not reset already-pending delivery records."""

    user = get_user_model().objects.create_user(username="alexa-reminder-owner")
    account = AlexaAccount.objects.create(
        name="Floor Alexa",
        user=user,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
    )
    reminder = AlexaReminder.objects.create(
        name="Network maintenance",
        event_key="network.maintenance",
        content="Maintenance starts in 10 minutes",
        user=user,
        scheduled_for=timezone.now(),
    )

    pending_delivery = AlexaReminderDelivery.objects.create(
        reminder=reminder,
        account=account,
        status=AlexaReminderDelivery.STATUS_PENDING,
    )
    sent_delivery = AlexaReminderDelivery.objects.create(
        reminder=reminder,
        account=AlexaAccount.objects.create(
            name="Lobby Alexa",
            user=user,
            client_id="client-id-2",
            client_secret="client-secret-2",
            refresh_token="refresh-token-2",
        ),
        status=AlexaReminderDelivery.STATUS_SENT,
    )

    updated = reminder.mark_event_update()

    pending_delivery.refresh_from_db()
    sent_delivery.refresh_from_db()
    assert updated == 1
    assert pending_delivery.status == AlexaReminderDelivery.STATUS_PENDING
    assert sent_delivery.status == AlexaReminderDelivery.STATUS_UPDATE_PENDING


@pytest.mark.django_db
def test_owner_scoped_uniqueness_for_accounts_and_reminders():
    """Duplicate names/event keys for the same owner should be rejected."""

    user = get_user_model().objects.create_user(username="alexa-uniqueness-owner")

    AlexaAccount.objects.create(
        name="Desk Alexa",
        user=user,
        client_id="id-a",
        client_secret="secret-a",
        refresh_token="refresh-a",
    )
    duplicate_account = AlexaAccount(
        name="Desk Alexa",
        user=user,
        client_id="id-b",
        client_secret="secret-b",
        refresh_token="refresh-b",
    )
    with pytest.raises(ValidationError):
        duplicate_account.full_clean()

    AlexaReminder.objects.create(
        name="Reminder A",
        event_key="event.same",
        content="content",
        user=user,
    )
    duplicate_reminder = AlexaReminder(
        name="Reminder B",
        event_key="event.same",
        content="content",
        user=user,
    )
    with pytest.raises(ValidationError):
        duplicate_reminder.full_clean()


@pytest.mark.django_db
def test_delivery_rejects_mismatched_owners():
    """Deliveries must not mix reminder/account ownership boundaries."""

    user_a = get_user_model().objects.create_user(username="owner-a")
    user_b = get_user_model().objects.create_user(username="owner-b")

    reminder = AlexaReminder.objects.create(
        name="Owner A reminder",
        event_key="owner.a",
        content="only for owner a",
        user=user_a,
    )
    account = AlexaAccount.objects.create(
        name="Owner B account",
        user=user_b,
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
    )

    delivery = AlexaReminderDelivery(reminder=reminder, account=account)
    with pytest.raises(ValidationError):
        delivery.full_clean()
