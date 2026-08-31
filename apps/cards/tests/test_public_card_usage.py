from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.cards.models import RFID, RFIDAttempt
from apps.cards.public_usage import OCPP_ID_TAG_LENGTH, build_public_rfid_usage
from apps.energy.models import CustomerAccount
from apps.ocpp.models import Charger, Transaction

pytestmark = pytest.mark.django_db


def test_public_card_usage_page_uses_token_and_hides_sensitive_values(client):
    tag = RFID.objects.create(rfid="A1B2C3D4", custom_label="Guest Card")
    tag.enable_public_usage()
    account = CustomerAccount.objects.create(name="Private Customer")
    charger = Charger.objects.create(
        charger_id="INTERNAL-CP-001",
        display_name="Lobby Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        account=account,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=2500,
        connector_id=2,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "label_id": tag.pk,
            "authorization_reason": "account_authorized",
            "diagnostic": "secret payload",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
        account_id=account.pk,
    )

    response = client.get(reverse("rfid-public-card", args=[tag.public_token]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Guest Card" in content
    assert "Lobby Charger connector 2" in content
    assert "1.50" in content
    assert tag.rfid not in content
    assert "Private Customer" not in content
    assert "secret payload" not in content
    assert "INTERNAL-CP-001" not in content


def test_public_card_usage_excludes_rejected_transactions():
    tag = RFID.objects.create(rfid="A1B2C3D4", custom_label="Guest Card")
    tag.enable_public_usage()
    charger = Charger.objects.create(charger_id="PUBLIC-CP-001")
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=2500,
    )
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(minutes=30),
        stop_time=now - timedelta(minutes=15),
        meter_start=1000,
        meter_stop=9000,
        authorization_status=Transaction.AuthorizationStatus.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["current_month_sessions"] == 1
    assert context["total_kwh"] == Decimal("1.5")
    assert [row["status"] for row in context["recent_transactions"]] == ["accepted"]


def test_public_card_usage_matches_prefix_reported_rfid_values():
    tag = RFID.objects.create(rfid="A1B2C3D4FFEE", custom_label="Guest Card")
    tag.enable_public_usage()
    charger = Charger.objects.create(charger_id="PUBLIC-CP-001")
    reported_rfid = tag.rfid[: RFID.MATCH_PREFIX_LENGTH].lower()
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=2500,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "authorization_reason": "account_authorized",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert context["recent_attempts"][0]["reason"] == "account authorized"


def test_public_card_usage_matches_ocpp_id_tag_prefix_for_long_rfid():
    tag = RFID.objects.create(
        rfid="A1B2C3D4E5F60718293A4B5C", custom_label="Guest Card"
    )
    tag.enable_public_usage()
    charger = Charger.objects.create(charger_id="PUBLIC-CP-001")
    reported_rfid = tag.rfid[:OCPP_ID_TAG_LENGTH].lower()
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=2500,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "authorization_reason": "account_authorized",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert context["recent_attempts"][0]["reason"] == "account authorized"


def test_public_card_usage_keeps_ocpp_id_tag_with_reversed_only_overlap():
    tag = RFID.objects.create(
        rfid="A1B2C3D4E5F60718293A4B5C", custom_label="Guest Card"
    )
    tag.enable_public_usage()
    other_tag = RFID.objects.create(
        rfid="FFFF3A291807F6E5D4C3B2A1",
        custom_label="Private Card",
    )
    reported_rfid = tag.rfid[:OCPP_ID_TAG_LENGTH]
    assert other_tag.reversed_uid.startswith(reported_rfid)
    charger = Charger.objects.create(
        charger_id="REVERSED-ONLY-OCPP-CP-001",
        display_name="Reversed Only OCPP Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=2500,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "authorization_reason": "account_authorized",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert context["recent_attempts"][0]["reason"] == "account authorized"


def test_public_card_usage_excludes_shared_ocpp_id_tag_prefix():
    tag = RFID.objects.create(
        rfid="A1B2C3D4E5F60718293A1111",
        custom_label="Guest Card",
    )
    tag.enable_public_usage()
    other_tag = RFID.objects.create(
        rfid="A1B2C3D4E5F60718293ABBBB",
        custom_label="Private Card",
    )
    reported_rfid = tag.rfid[:OCPP_ID_TAG_LENGTH].lower()
    charger = Charger.objects.create(
        charger_id="SHARED-OCPP-PREFIX-CP-001",
        display_name="Shared OCPP Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "shared_ocpp_prefix_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_ocpp_prefix_owned_by_shorter_card():
    tag = RFID.objects.create(
        rfid="FACEB00C1234567890ABCDEF",
        custom_label="Guest Card",
    )
    tag.enable_public_usage()
    mid_prefix_tag = RFID.objects.create(rfid="FACEB00C12", custom_label="Mid Card")
    reported_rfid = tag.rfid[:OCPP_ID_TAG_LENGTH]
    charger = Charger.objects.create(
        charger_id="OCPP-MID-PREFIX-CP-001",
        display_name="OCPP Mid Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "label_id": mid_prefix_tag.pk,
            "authorization_reason": "ocpp_mid_prefix_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_other_cards_sharing_prefix():
    tag = RFID.objects.create(rfid="FACEB00CAAAA", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(rfid="FACEB00CBBBB", custom_label="Private Card")
    charger = Charger.objects.create(
        charger_id="PRIVATE-CP-001",
        display_name="Private Card B Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=other_tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": other_tag.rfid,
            "authorization_reason": "private_b_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_exact_value_with_ambiguous_resolver_prefix():
    tag = RFID.objects.create(rfid="FACEB00CFFFF", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(rfid="FACEB00CAAAA", custom_label="Private Card")
    charger = Charger.objects.create(
        charger_id="AMBIGUOUS-EXACT-CP-001",
        display_name="Ambiguous Exact Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "ambiguous_exact_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_ocpp_value_with_ambiguous_resolver_prefix():
    tag = RFID.objects.create(
        rfid="FACEB00CFFFF123456789ABC",
        custom_label="Guest Card",
    )
    tag.enable_public_usage()
    other_tag = RFID.objects.create(
        rfid="FACEB00CAAAA123456789ABC",
        custom_label="Private Card",
    )
    reported_rfid = tag.rfid[:OCPP_ID_TAG_LENGTH]
    charger = Charger.objects.create(
        charger_id="AMBIGUOUS-OCPP-CP-001",
        display_name="Ambiguous OCPP Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "ambiguous_ocpp_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_prefix_reports_shared_by_long_cards():
    tag = RFID.objects.create(rfid="FACEB00CAAAA", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(rfid="FACEB00CBBBB", custom_label="Private Card")
    reported_rfid = tag.rfid[: RFID.MATCH_PREFIX_LENGTH]
    charger = Charger.objects.create(
        charger_id="SHARED-PREFIX-CP-001",
        display_name="Shared Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "shared_prefix_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_reversed_prefix_reports_shared_by_cards():
    tag = RFID.objects.create(rfid="11111111FACEB00C", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(
        rfid="22222222FACEB00C", custom_label="Private Card"
    )
    reported_rfid = RFID.reverse_uid(tag.rfid)[: RFID.MATCH_PREFIX_LENGTH]
    charger = Charger.objects.create(
        charger_id="REVERSED-PREFIX-CP-001",
        display_name="Reversed Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "reversed_prefix_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_keeps_exact_uid_with_suffix_only_collision():
    tag = RFID.objects.create(rfid="11111111FACEB00C", custom_label="Guest Card")
    tag.enable_public_usage()
    RFID.objects.create(rfid="22222222FACEB00C", custom_label="Private Card")
    charger = Charger.objects.create(
        charger_id="SUFFIX-ONLY-CP-001",
        display_name="Suffix Only Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "authorization_reason": "suffix_only_accepted_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert context["recent_attempts"][0]["reason"] == "suffix only accepted reason"


def test_public_card_usage_keeps_short_uid_that_prefixes_other_reversed_uid():
    tag = RFID.objects.create(rfid="0CB0CEFA", custom_label="Guest Card")
    tag.enable_public_usage()
    RFID.objects.create(rfid="22222222FACEB00C", custom_label="Private Card")
    charger = Charger.objects.create(
        charger_id="SHORT-REVERSED-PREFIX-CP-001",
        display_name="Short Reversed Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "authorization_reason": "short_reversed_prefix_accepted_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert (
        context["recent_attempts"][0]["reason"]
        == "short reversed prefix accepted reason"
    )


def test_public_card_usage_keeps_exact_uid_with_reversed_only_prefix_overlap():
    tag = RFID.objects.create(rfid="0CB0CEFA1111", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(rfid="FACEB00C", custom_label="Private Card")
    assert tag.rfid.startswith(other_tag.reversed_uid)
    charger = Charger.objects.create(
        charger_id="EXACT-REVERSED-ONLY-PREFIX-CP-001",
        display_name="Exact Reversed Only Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "authorization_reason": "exact_reversed_only_prefix_accepted_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert (
        context["recent_attempts"][0]["reason"]
        == "exact reversed only prefix accepted reason"
    )


def test_public_card_usage_keeps_ocpp_id_tag_with_short_reversed_only_overlap():
    tag = RFID.objects.create(
        rfid="0CB0CEFA1111222233334444",
        custom_label="Guest Card",
    )
    tag.enable_public_usage()
    other_tag = RFID.objects.create(rfid="FACEB00C", custom_label="Private Card")
    reported_rfid = tag.rfid[:OCPP_ID_TAG_LENGTH]
    assert reported_rfid.startswith(other_tag.reversed_uid)
    charger = Charger.objects.create(
        charger_id="OCPP-REVERSED-ONLY-PREFIX-CP-001",
        display_name="OCPP Reversed Only Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "authorization_reason": "ocpp_reversed_only_prefix_accepted_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert (
        context["recent_attempts"][0]["reason"]
        == "ocpp reversed only prefix accepted reason"
    )


def test_public_card_usage_matches_longer_ocpp_id_tag_for_short_rfid():
    tag = RFID.objects.create(rfid="FACEB00C", custom_label="Guest Card")
    tag.enable_public_usage()
    reported_rfid = f"{tag.rfid}1234567890AB"
    charger = Charger.objects.create(
        charger_id="SHORT-RFID-LONG-IDTAG-CP-001",
        display_name="Short RFID Long IdTag Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "authorization_reason": "short_rfid_long_idtag_accepted_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert (
        context["recent_attempts"][0]["reason"]
        == "short rfid long idtag accepted reason"
    )


def test_public_card_usage_matches_longer_ocpp_id_tag_for_mid_length_rfid():
    tag = RFID.objects.create(rfid="FACEB00C12", custom_label="Guest Card")
    tag.enable_public_usage()
    reported_rfid = f"{tag.rfid}34567890AB"
    charger = Charger.objects.create(
        charger_id="MID-RFID-LONG-IDTAG-CP-001",
        display_name="Mid RFID Long IdTag Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "authorization_reason": "mid_rfid_long_idtag_accepted_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert (
        context["recent_attempts"][0]["reason"] == "mid rfid long idtag accepted reason"
    )


def test_public_card_usage_matches_same_resolver_prefix_id_tag_for_mid_length_rfid():
    tag = RFID.objects.create(rfid="FACEB00C12", custom_label="Guest Card")
    tag.enable_public_usage()
    reported_rfid = "FACEB00CFFEE1234"
    charger = Charger.objects.create(
        charger_id="MID-RFID-SAME-PREFIX-IDTAG-CP-001",
        display_name="Mid RFID Same Prefix IdTag Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "authorization_reason": "mid_rfid_same_prefix_idtag_accepted_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.ACCEPTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 1
    assert context["accepted_scan_count"] == 1
    assert context["recent_transactions"][0]["status"] == "accepted"
    assert (
        context["recent_attempts"][0]["reason"]
        == "mid rfid same prefix idtag accepted reason"
    )


def test_public_card_usage_excludes_prefix_id_tag_claimed_as_reversed_uid():
    tag = RFID.objects.create(rfid="0CB0CEFA1111", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(rfid="FACEB00C", custom_label="Private Card")
    reported_rfid = other_tag.reversed_uid
    assert tag.rfid.startswith(reported_rfid)
    charger = Charger.objects.create(
        charger_id="PREFIX-REVERSED-OWNER-CP-001",
        display_name="Prefix Reversed Owner Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=reported_rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": reported_rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "prefix_reversed_owner_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_exact_reversed_uid_claimed_by_other_card():
    tag = RFID.objects.create(rfid="AABBCCDDEEFF", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(
        rfid=RFID.reverse_uid(tag.rfid), custom_label="Private Card"
    )
    charger = Charger.objects.create(
        charger_id="REVERSED-EXACT-CP-001",
        display_name="Reversed Exact Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=other_tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": other_tag.rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "reversed_exact_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_canonical_uid_claimed_as_other_reversed_uid():
    tag = RFID.objects.create(rfid="AABBCCDDEEFF", custom_label="Guest Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(
        rfid=RFID.reverse_uid(tag.rfid), custom_label="Private Card"
    )
    charger = Charger.objects.create(
        charger_id="CANONICAL-REVERSED-CP-001",
        display_name="Canonical Reversed Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "canonical_reversed_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_short_card_that_owns_prefix():
    tag = RFID.objects.create(rfid="FACEB00CAAAA", custom_label="Guest Card")
    tag.enable_public_usage()
    short_tag = RFID.objects.create(rfid="FACEB00C", custom_label="Short Card")
    charger = Charger.objects.create(
        charger_id="SHORT-CP-001",
        display_name="Short Card Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=short_tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": short_tag.rfid,
            "label_id": short_tag.pk,
            "authorization_reason": "short_card_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_exact_uid_when_short_card_owns_prefix():
    tag = RFID.objects.create(rfid="FACEB00CAAAA", custom_label="Guest Card")
    tag.enable_public_usage()
    short_tag = RFID.objects.create(rfid="FACEB00C", custom_label="Short Card")
    charger = Charger.objects.create(
        charger_id="LONG-UID-SHORT-PREFIX-CP-001",
        display_name="Long UID Short Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "label_id": short_tag.pk,
            "authorization_reason": "short_prefix_owner_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_exact_uid_when_mid_length_card_owns_prefix():
    tag = RFID.objects.create(rfid="FACEB00C123456", custom_label="Guest Card")
    tag.enable_public_usage()
    mid_prefix_tag = RFID.objects.create(rfid="FACEB00C12", custom_label="Mid Card")
    charger = Charger.objects.create(
        charger_id="LONG-UID-MID-PREFIX-CP-001",
        display_name="Long UID Mid Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "label_id": mid_prefix_tag.pk,
            "authorization_reason": "mid_prefix_owner_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_excludes_exact_value_claimed_as_other_card_prefix():
    tag = RFID.objects.create(rfid="FACEB00C", custom_label="Short Card")
    tag.enable_public_usage()
    other_tag = RFID.objects.create(rfid="FACEB00CAAAA", custom_label="Long Card")
    charger = Charger.objects.create(
        charger_id="LONG-PREFIX-CP-001",
        display_name="Long Prefix Charger",
    )
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        rfid=tag.rfid,
        start_time=now - timedelta(hours=2),
        stop_time=now - timedelta(hours=1),
        meter_start=1000,
        meter_stop=4750,
        connector_id=7,
    )
    RFIDAttempt.record_attempt(
        {
            "rfid": tag.rfid,
            "label_id": other_tag.pk,
            "authorization_reason": "long_prefix_rejected_reason",
        },
        source=RFIDAttempt.Source.OCPP,
        status=RFIDAttempt.Status.REJECTED,
    )

    context = build_public_rfid_usage(tag)

    assert context["total_sessions"] == 0
    assert context["total_kwh"] == Decimal("0")
    assert context["rejected_scan_count"] == 0
    assert context["recent_transactions"] == []
    assert context["recent_attempts"] == []


def test_public_card_usage_fallback_label_is_not_rfid_derived():
    tag = RFID.objects.create(rfid="A1B2C3D4")
    tag.enable_public_usage()

    context = build_public_rfid_usage(tag)

    assert tag.name_key
    assert context["display_label"] == "RFID card"
    assert context["display_label"] != tag.name_key


def test_public_card_usage_disabled_token_returns_404(client):
    tag = RFID.objects.create(rfid="A1B2C3D4", custom_label="Guest Card")
    tag.enable_public_usage()
    token = tag.public_token
    tag.disable_public_usage()

    response = client.get(reverse("rfid-public-card", args=[token]))

    assert response.status_code == 404


def test_public_card_usage_rotation_revokes_old_token(client):
    tag = RFID.objects.create(rfid="A1B2C3D4", custom_label="Guest Card")
    tag.enable_public_usage()
    old_token = tag.public_token
    tag.rotate_public_token()

    old_response = client.get(reverse("rfid-public-card", args=[old_token]))
    new_response = client.get(reverse("rfid-public-card", args=[tag.public_token]))

    assert old_response.status_code == 404
    assert new_response.status_code == 200


def test_public_usage_token_is_not_raw_rfid():
    tag = RFID.objects.create(rfid="A1B2C3D4")

    tag.enable_public_usage()

    assert tag.public_token
    assert tag.public_token != tag.rfid
    assert tag.public_usage_path() == reverse(
        "rfid-public-card", args=[tag.public_token]
    )


def test_public_usage_empty_public_token_persists_with_update_fields():
    tag = RFID.objects.create(rfid="A1B2C3D4", public_token="public-token")

    tag.public_token = ""
    tag.custom_label = "Updated"
    tag.save(update_fields=["custom_label"])
    tag.refresh_from_db()

    assert tag.public_token is None
    assert tag.custom_label == "Updated"


def test_public_usage_admin_qr_sheet_uses_token_url(client):
    admin_user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    client.force_login(admin_user)
    tag = RFID.objects.create(rfid="A1B2C3D4", custom_label="Guest Card")
    tag.enable_public_usage()

    response = client.get(reverse("admin:cards_rfid_public_usage_qr", args=[tag.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Guest Card" in content
    assert "Download PNG" in content
    assert "docs/js/qrcode.min.js" in content
    assert reverse("rfid-public-card", args=[tag.public_token]) in content
    assert tag.rfid not in content


def test_public_usage_admin_qr_sheet_requires_enabled_token(client):
    admin_user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    client.force_login(admin_user)
    tag = RFID.objects.create(rfid="A1B2C3D4", custom_label="Guest Card")
    tag.enable_public_usage()
    tag.disable_public_usage()

    response = client.get(reverse("admin:cards_rfid_public_usage_qr", args=[tag.pk]))

    assert response.status_code == 302
    assert response["Location"].endswith(
        reverse("admin:cards_rfid_change", args=[tag.pk])
    )


def test_public_usage_admin_qr_sheet_requires_rfid_permission(client):
    staff_user = get_user_model().objects.create_user(
        username="staff",
        email="staff@example.com",
        password="pass",
        is_staff=True,
    )
    client.force_login(staff_user)
    tag = RFID.objects.create(rfid="A1B2C3D4", custom_label="Guest Card")
    tag.enable_public_usage()

    response = client.get(reverse("admin:cards_rfid_public_usage_qr", args=[tag.pk]))

    assert response.status_code == 403
