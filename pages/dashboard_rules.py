"""Dashboard rule helpers and evaluators."""

from datetime import timedelta
from importlib import import_module
import logging
from typing import Callable

from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

from ocpp.models import Charger, ChargerConfiguration, CPFirmware
from nodes.models import Node

logger = logging.getLogger(__name__)

DEFAULT_SUCCESS_MESSAGE = _("All rules met.")
SUCCESS_ICON = "\u2713"
ERROR_ICON = "\u2717"


def rule_success(message: str = DEFAULT_SUCCESS_MESSAGE) -> dict[str, object]:
    return {"success": True, "message": message, "icon": SUCCESS_ICON}


def rule_failure(message: str) -> dict[str, object]:
    return {"success": False, "message": message, "icon": ERROR_ICON}


def _format_evcs_list(evcs_identifiers: list[str]) -> str:
    """Return a human-readable list of EVCS identifiers."""

    return ", ".join(evcs_identifiers)


def evaluate_cp_configuration_rules() -> dict[str, object] | None:
    chargers = list(
        Charger.objects.filter(connector_id__isnull=True)
        .order_by("charger_id")
        .values_list("charger_id", flat=True)
    )
    charger_ids = [identifier for identifier in chargers if identifier]
    if not charger_ids:
        return rule_success()

    configured = set(
        ChargerConfiguration.objects.filter(charger_identifier__in=charger_ids)
        .values_list("charger_identifier", flat=True)
    )
    missing = [identifier for identifier in charger_ids if identifier not in configured]
    if missing:
        evcs_list = _format_evcs_list(missing)
        message = ngettext(
            "Missing CP Configuration for %(evcs)s.",
            "Missing CP Configurations for %(evcs)s.",
            len(missing),
        ) % {"evcs": evcs_list}
        return rule_failure(message)

    return rule_success()


def evaluate_cp_firmware_rules() -> dict[str, object] | None:
    chargers = list(
        Charger.objects.filter(connector_id__isnull=True)
        .order_by("charger_id")
        .values_list("charger_id", flat=True)
    )
    charger_ids = [identifier for identifier in chargers if identifier]
    if not charger_ids:
        return rule_success()

    firmware_sources = set(
        CPFirmware.objects.filter(
            source_charger__isnull=False,
            source_charger__charger_id__in=charger_ids,
        ).values_list("source_charger__charger_id", flat=True)
    )
    missing = [identifier for identifier in charger_ids if identifier not in firmware_sources]
    if missing:
        evcs_list = _format_evcs_list(missing)
        message = ngettext(
            "Missing CP Firmware for %(evcs)s.",
            "Missing CP Firmware for %(evcs)s.",
            len(missing),
        ) % {"evcs": evcs_list}
        return rule_failure(message)

    return rule_success()


def evaluate_evcs_heartbeat_rules() -> dict[str, object] | None:
    cutoff = timezone.now() - timedelta(hours=1)
    chargers = list(
        Charger.objects.filter(connector_id__isnull=True)
        .order_by("charger_id")
        .values_list("charger_id", "last_heartbeat")
    )
    registered = [
        (identifier, heartbeat)
        for identifier, heartbeat in chargers
        if identifier and heartbeat is not None
    ]
    if not registered:
        return rule_success()

    missing = [identifier for identifier, heartbeat in registered if heartbeat < cutoff]
    if missing:
        evcs_list = _format_evcs_list(missing)
        message = ngettext(
            "Missing EVCS heartbeat within the last hour for %(evcs)s.",
            "Missing EVCS heartbeats within the last hour for %(evcs)s.",
            len(missing),
        ) % {"evcs": evcs_list}
        return rule_failure(message)

    return rule_success()


def evaluate_node_rules() -> dict[str, object]:
    local_node = Node.get_local()
    if local_node is None:
        return rule_failure(_("Local node record is missing."))

    if not getattr(local_node, "role_id", None):
        return rule_failure(_("Local node is missing an assigned role."))

    is_watchtower = (local_node.role.name or "").lower() == "watchtower"

    if not is_watchtower:
        upstream_nodes = Node.objects.filter(current_relation=Node.Relation.UPSTREAM)
        if not upstream_nodes.exists():
            return rule_failure(_("At least one upstream node is required."))

        recent_cutoff = timezone.now() - timedelta(hours=24)
        if not upstream_nodes.filter(last_seen__gte=recent_cutoff).exists():
            return rule_failure(
                _("No upstream nodes have checked in within the last 24 hours."),
            )

    return rule_success()


def evaluate_email_profile_rules() -> dict[str, object]:
    try:
        from teams.models import EmailInbox, EmailOutbox
    except Exception:
        logger.exception("Unable to import email profile models")
        return rule_failure(_("Unable to evaluate email configuration."))

    try:
        inboxes = list(EmailInbox.objects.filter(is_enabled=True))
        outboxes = list(EmailOutbox.objects.filter(is_enabled=True))
    except Exception:
        logger.exception("Unable to query email profiles")
        return rule_failure(_("Unable to evaluate email configuration."))

    ready_inboxes = [inbox for inbox in inboxes if inbox.is_ready()]
    ready_outboxes = [outbox for outbox in outboxes if outbox.is_ready()]

    issues: list[str] = []
    if not inboxes:
        issues.append(_("At least one Email Inbox must be configured."))
    elif not ready_inboxes:
        issues.append(_("Configured Email Inboxes could not complete validation."))

    if not outboxes:
        issues.append(_("At least one Email Outbox must be configured."))
    elif not ready_outboxes:
        issues.append(_("Configured Email Outboxes could not complete validation."))

    if issues:
        return rule_failure(" ".join(issues))

    return rule_success(_("Email inbox and outbox are validated for this node."))


def load_callable(handler_name: str) -> Callable[[], dict[str, object]] | None:
    if not handler_name:
        return None

    try:
        module = import_module(__name__)
    except Exception:  # pragma: no cover - import errors surface as runtime failures
        logger.exception("Unable to import dashboard rule module")
        return None

    return getattr(module, handler_name, None)
