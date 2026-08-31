"""Shared account provisioning for OCPP auto-start identifiers."""

from __future__ import annotations

from django.core.management.base import CommandError

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount

RFID_FALLBACK_ACCOUNT_NAME = "RFID FALLBACK ACCOUNT"


def get_or_create_auto_start_account(id_tag: str) -> tuple[CustomerAccount, bool]:
    """Return the service account that attributes an auto-start idTag."""

    normalized_id_tag = RFID.normalize_code(id_tag)
    rfid_candidates = {normalized_id_tag, RFID.reverse_uid(normalized_id_tag)}
    if RFID.objects.filter(rfid__in=rfid_candidates - {""}).exists():
        raise CommandError(
            f"Cannot enable auto-start: idTag '{id_tag}' conflicts with an RFID."
        )

    existing = CustomerAccount.objects.filter(ocpp_id_tag=id_tag).first()
    if existing is not None:
        if not existing.service_account:
            raise CommandError(
                f"Cannot enable auto-start: idTag '{id_tag}' belongs to a non-service account."
            )
        return existing, False

    base_name = f"AUTO-START {id_tag}".upper()
    candidate_name = base_name
    suffix = 2
    while CustomerAccount.objects.filter(name=candidate_name).exists():
        candidate_name = f"{base_name} {suffix}"
        suffix += 1
    account, created = CustomerAccount.objects.get_or_create(
        ocpp_id_tag=id_tag,
        defaults={
            "name": candidate_name,
            "service_account": True,
        },
    )
    if not account.service_account:
        raise CommandError(
            f"Cannot enable auto-start: idTag '{id_tag}' belongs to a non-service account."
        )
    return account, created


def get_or_create_rfid_fallback_account() -> tuple[CustomerAccount, bool]:
    """Return the service account used by profile-declared fallback cards."""

    account, created = CustomerAccount.objects.get_or_create(
        name=RFID_FALLBACK_ACCOUNT_NAME,
        defaults={"service_account": True},
    )
    if not account.service_account:
        raise CommandError(
            "Cannot configure RFID fallback: the fallback account is not a service account."
        )
    return account, created
