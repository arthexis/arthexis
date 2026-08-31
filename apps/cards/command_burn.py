from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from apps.cards.command_layout import decode_command_card_from_dump
from apps.cards.models import RFID, RFIDAttempt, RFIDCommandTemplate

BURN_COMMAND_TEMPLATE_NAME = "BURN RFID CARD"
BURN_COMMAND_TEMPLATE_SLUG = "burn-rfid-card"
BURN_COMMAND_TEMPLATE_QR_PATH = "/cards/command-templates/burn/"
DEFAULT_PREVIOUS_SCAN_LIMIT = 50
DEFAULT_COMMAND_CARD_BURN_TIMEOUT = 30.0


class CommandCardBurnError(ValueError):
    """Raised when a command-card burn source cannot be resolved."""


@dataclass(frozen=True, slots=True)
class CommandCardBurnSource:
    template: RFIDCommandTemplate
    source_rfid: RFID | None = None
    selected: bool = False


def command_template_queryset(*, include_inactive: bool = False):
    queryset = RFIDCommandTemplate.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("source", "name")


def get_command_template_for_burn(value: str) -> RFIDCommandTemplate:
    cleaned = (value or "").strip()
    if not cleaned:
        raise CommandCardBurnError("Command template is required")
    template = (
        RFIDCommandTemplate.objects.filter(
            Q(name__iexact=cleaned.upper()) | Q(slug__iexact=cleaned)
        )
        .order_by("source", "name")
        .first()
    )
    if template is None:
        raise CommandCardBurnError(f"No RFID command template found for '{cleaned}'")
    if not template.is_active:
        raise CommandCardBurnError(
            f"RFID command template '{template.name}' is inactive"
        )
    return template


def template_for_rfid_command_card(tag: RFID) -> RFIDCommandTemplate | None:
    if tag.command_template_id:
        return tag.command_template
    if tag.command_card_name:
        template = RFIDCommandTemplate.objects.filter(
            name=tag.command_card_name
        ).first()
        if template is not None:
            return template
    if tag.data:
        card = decode_command_card_from_dump(tag.data)
        if card is not None:
            template = RFIDCommandTemplate.for_card(card)
            if template is not None:
                return template
            discovered_template, _created = RFIDCommandTemplate.discover_from_card(card)
            return discovered_template
    return None


def _tag_from_attempt(attempt: RFIDAttempt) -> RFID | None:
    if attempt.label_id:
        return attempt.label
    rfid_value = RFID.normalize_code(str(attempt.rfid or ""))
    return RFID.find_match(rfid_value) if rfid_value else None


def latest_scanned_command_card_source(
    *,
    limit: int = DEFAULT_PREVIOUS_SCAN_LIMIT,
) -> CommandCardBurnSource | None:
    from apps.cards.scanner import SCANNER_SOURCES, ingest_service_scans

    ingest_service_scans()
    seen: set[int] = set()
    attempts = (
        RFIDAttempt.objects.filter(source__in=SCANNER_SOURCES)
        .select_related("label", "label__command_template")
        .order_by("-attempted_at", "-pk")[: max(1, limit)]
    )
    for attempt in attempts:
        tag = _tag_from_attempt(attempt)
        if tag is None or tag.pk in seen:
            continue
        seen.add(tag.pk)
        template = template_for_rfid_command_card(tag)
        if template is None or not template.is_active:
            continue
        if template.slug == BURN_COMMAND_TEMPLATE_SLUG:
            continue
        return CommandCardBurnSource(template=template, source_rfid=tag)
    return None


def resolve_command_card_burn_source(
    selected_template: str | None = None,
) -> CommandCardBurnSource:
    if selected_template:
        return CommandCardBurnSource(
            template=get_command_template_for_burn(selected_template),
            selected=True,
        )
    source = latest_scanned_command_card_source()
    if source is None:
        raise CommandCardBurnError("No previous command-card template scan found")
    return source
