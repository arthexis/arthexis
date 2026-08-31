"""Shared constants and helpers for OCPP Celery tasks."""

from datetime import timedelta

from apps.emails.utils import resolve_recipient_fallbacks
from apps.nodes.models import Node
from apps.ocpp.models import Charger

DEFAULT_FIRMWARE_VENDOR_ID = "org.openchargealliance.firmware"
OFFLINE_NOTIFICATION_GRACE = timedelta(minutes=5)
OFFLINE_NOTIFICATION_COOLDOWN = timedelta(days=1)


def first_group_email(group) -> str:
    """Return the first active user's email for ``group``."""

    if not group or not getattr(group, "pk", None):
        return ""
    user = (
        group.user_set.filter(is_active=True)
        .exclude(email="")
        .order_by("id")
        .first()
    )
    return (user.email or "").strip() if user else ""


def node_outbox_email() -> str:
    """Return the email address of the local node outbox owner."""

    node = Node.get_local()
    if not node:
        return ""
    outbox = getattr(node, "email_outbox", None)
    if not outbox:
        return ""
    owner = getattr(outbox, "owner", None)
    if owner is None:
        return ""
    if hasattr(owner, "email"):
        return (getattr(owner, "email", "") or "").strip()
    return first_group_email(owner)


def session_report_recipients() -> list[str]:
    """Return recipients for the daily session report."""

    recipients, _ = resolve_recipient_fallbacks([], owner=None)
    return recipients


def resolve_offline_notification_recipient(charger: Charger) -> str:
    """Resolve a recipient for offline notifications for ``charger``."""

    maintenance_email = charger.maintenance_email_value()
    if maintenance_email:
        return maintenance_email
    owner = charger.owner
    if owner is not None and hasattr(owner, "email"):
        owner_email = (getattr(owner, "email", "") or "").strip()
        if owner_email:
            return owner_email
    group_email = first_group_email(getattr(charger, "group", None))
    if group_email:
        return group_email
    outbox_email = node_outbox_email()
    if outbox_email:
        return outbox_email
    recipients, _ = resolve_recipient_fallbacks([], owner=None)
    return recipients[0] if recipients else ""
