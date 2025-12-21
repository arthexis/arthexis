import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from importlib import import_module
from typing import Callable

from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _, ngettext

from apps.ocpp.models import Charger, ChargerConfiguration, CPFirmware
from apps.nodes.models import Node
from apps.nginx.models import SiteConfiguration

logger = logging.getLogger(__name__)

DEFAULT_SUCCESS_MESSAGE = _("All rules met.")
SUCCESS_ICON = "\u2713"
ERROR_ICON = "\u2717"
_RULE_MODEL_CONTEXT: ContextVar[str | None] = ContextVar(
    "dashboard_rule_model", default=None
)

# Keep dashboard rule messaging consistent: success responses should use
# ``DEFAULT_SUCCESS_MESSAGE`` and error text should stay short enough to fit
# dashboard UI constraints. Extend new rules with the same uniform success
# message and concise error strings rather than introducing custom phrases.


def rule_success(message: str = DEFAULT_SUCCESS_MESSAGE) -> dict[str, object]:
    """Return a serializable success payload for dashboard rules."""

    return {"success": True, "message": str(message), "icon": SUCCESS_ICON}


def rule_failure(message: str) -> dict[str, object]:
    """Return a serializable failure payload for dashboard rules."""

    return {"success": False, "message": str(message), "icon": ERROR_ICON}


@contextmanager
def bind_rule_model(model_identifier: str | None):
    token = _RULE_MODEL_CONTEXT.set(model_identifier)
    try:
        yield
    finally:
        _RULE_MODEL_CONTEXT.reset(token)


def current_rule_model() -> str | None:
    return _RULE_MODEL_CONTEXT.get()


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
            "Missing CP config: %(evcs)s.",
            "Missing CP configs: %(evcs)s.",
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
            "Missing firmware: %(evcs)s.",
            "Missing firmware: %(evcs)s.",
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
            "Heartbeat overdue: %(evcs)s.",
            "Heartbeat overdue: %(evcs)s.",
            len(missing),
        ) % {"evcs": evcs_list}
        return rule_failure(message)

    return rule_success()


def evaluate_node_rules() -> dict[str, object]:
    local_node = Node.get_local()
    if local_node is None:
        return rule_failure(_("Local node record is missing."))

    if not getattr(local_node, "role_id", None):
        return rule_failure(_("Local node missing a role."))

    is_watchtower = (local_node.role.name or "").lower() == "watchtower"

    if not is_watchtower:
        upstream_nodes = Node.objects.filter(current_relation=Node.Relation.UPSTREAM)
        if not upstream_nodes.exists():
            return rule_failure(_("Need an upstream node."))

        recent_cutoff = timezone.now() - timedelta(hours=24)
        if not upstream_nodes.filter(last_seen__gte=recent_cutoff).exists():
            return rule_failure(
                _("No check-ins in last 24 hours."),
            )

    return rule_success()


def evaluate_email_profile_rules() -> dict[str, object]:
    try:
        from apps.emails.models import EmailInbox, EmailOutbox
    except Exception:
        logger.exception("Unable to import email profile models")
        return rule_failure(_("Email check failed: import err."))

    try:
        inboxes = list(EmailInbox.objects.filter(is_enabled=True))
        outboxes = list(EmailOutbox.objects.filter(is_enabled=True))
    except Exception:
        logger.exception("Unable to query email profiles")
        return rule_failure(_("Email check failed: db error."))

    model_key = (current_rule_model() or "").lower()
    evaluate_inbox = model_key.endswith("emailinbox")
    evaluate_outbox = model_key.endswith("emailoutbox")
    if not (evaluate_inbox or evaluate_outbox):
        evaluate_inbox = evaluate_outbox = True

    issues: list[str] = []
    if evaluate_inbox:
        ready_inboxes = [inbox for inbox in inboxes if inbox.is_ready()]
        if not inboxes:
            issues.append(_("Configure an Email Inbox."))
        elif not ready_inboxes:
            issues.append(_("Email Inbox validation failed."))

    if evaluate_outbox:
        ready_outboxes = [outbox for outbox in outboxes if outbox.is_ready()]
        if not outboxes:
            issues.append(_("Configure an Email Outbox."))
        elif not ready_outboxes:
            issues.append(_("Email Outbox validation failed."))

    if issues:
        return rule_failure(" ".join(str(issue) for issue in issues))

    return rule_success()


def evaluate_nginx_site_configuration_rules() -> dict[str, object] | None:
    if not SiteConfiguration.objects.filter(name="default").exists():
        return rule_failure(_("Missing default site config."))

    enabled_sites = list(SiteConfiguration.objects.filter(enabled=True))
    if not enabled_sites:
        return rule_failure(_("Enable at least one site."))

    cutoff = timezone.now() - timedelta(days=3)
    recent_validation = False
    for site in enabled_sites:
        last_activity = max(
            (timestamp for timestamp in [site.last_applied_at, site.last_validated_at] if timestamp),
            default=None,
        )
        if last_activity and last_activity >= cutoff:
            recent_validation = True
            break

    if not recent_validation:
        return rule_failure(_("Site validation is stale."))

    return rule_success()


def load_callable(handler_name: str) -> Callable[[], dict[str, object]] | None:
    if not handler_name:
        return None

    try:
        module = import_module(__name__)
    except Exception:  # pragma: no cover - import errors surface as runtime failures
        logger.exception("Unable to import dashboard rule module")
        return None

    return getattr(module, handler_name, None)
