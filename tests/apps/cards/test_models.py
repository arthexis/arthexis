from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.energy.models import CustomerAccount

pytestmark = pytest.mark.django_db


def test_string_uses_label_then_external_id() -> None:
    labelled = CardCredential.objects.create(
        external_id="card-labelled",
        label="Front Desk",
    )
    unlabelled = CardCredential.objects.create(
        external_id="card-unlabelled",
    )

    assert str(labelled) == "Front Desk"
    assert str(unlabelled) == "card-unlabelled"


def test_credentials_are_ordered_by_external_id() -> None:
    CardCredential.objects.create(external_id="card-z")
    CardCredential.objects.create(external_id="card-a")

    assert list(
        CardCredential.objects.values_list("external_id", flat=True)
    ) == ["card-a", "card-z"]


def test_external_id_is_unique() -> None:
    CardCredential.objects.create(external_id="duplicate")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CardCredential.objects.create(external_id="duplicate")


def test_optional_user_and_account_links_are_retained_when_present() -> None:
    user = get_user_model().objects.create_user(
        username="card-owner",
        password="unused",
    )
    account = CustomerAccount.objects.create(
        key="account-1",
        name="Account",
    )

    credential = CardCredential.objects.create(
        external_id="card-1",
        user=user,
        account=account,
    )

    assert credential.user == user
    assert credential.account == account
    assert user.card_credentials.get() == credential
    assert account.card_credentials.get() == credential


def test_user_and_account_deletion_leave_the_credential() -> None:
    user = get_user_model().objects.create_user(
        username="card-owner",
        password="unused",
    )
    account = CustomerAccount.objects.create(
        key="account-1",
        name="Account",
    )
    credential = CardCredential.objects.create(
        external_id="card-1",
        user=user,
        account=account,
    )

    user.delete()
    account.delete()

    credential.refresh_from_db()
    assert credential.user is None
    assert credential.account is None


def test_defaults_describe_an_active_unassigned_credential() -> None:
    credential = CardCredential.objects.create(external_id="card-1")

    assert credential.active
    assert credential.label == ""
    assert credential.ocpp_id_tag == ""
    assert credential.user is None
    assert credential.account is None


def test_attempt_records_decision_details() -> None:
    credential = CardCredential.objects.create(external_id="card-1")

    attempt = AuthorizationAttempt.objects.create(
        card=credential,
        presented_id="presented-card",
        accepted=True,
        reason="matched_credential",
    )

    assert attempt.card == credential
    assert attempt.presented_id == "presented-card"
    assert attempt.accepted
    assert attempt.reason == "matched_credential"
    assert attempt.occurred_at is not None


def test_attempt_survives_credential_deletion() -> None:
    credential = CardCredential.objects.create(external_id="card-1")
    attempt = AuthorizationAttempt.objects.create(
        card=credential,
        presented_id="card-1",
    )

    credential.delete()

    attempt.refresh_from_db()
    assert attempt.card is None
    assert attempt.presented_id == "card-1"


def test_attempt_defaults_record_a_rejected_unexplained_decision() -> None:
    attempt = AuthorizationAttempt.objects.create(presented_id="unknown")

    assert not attempt.accepted
    assert attempt.reason == ""
    assert attempt.card is None


def test_attempts_are_ordered_newest_first() -> None:
    older = AuthorizationAttempt.objects.create(presented_id="older")
    AuthorizationAttempt.objects.create(presented_id="newer")
    AuthorizationAttempt.objects.filter(pk=older.pk).update(
        occurred_at=timezone.now() - timedelta(hours=1)
    )

    assert list(
        AuthorizationAttempt.objects.values_list("presented_id", flat=True)
    ) == ["newer", "older"]
