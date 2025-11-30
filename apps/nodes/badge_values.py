from django.db.models import Count, Exists, OuterRef, Q
from django.utils.translation import gettext, ngettext

from apps.core.models import Lead, RFID
from apps.ocpp.models import Charger
from apps.nodes.models import Node
from .badge_utils import BadgeCounterResult


def open_lead_count(badge=None):
    model_class = None
    if badge is not None:
        content_type = getattr(badge, "content_type", None)
        model_class = content_type.model_class() if content_type else None
    if model_class is None:
        return None

    concrete_model = model_class._meta.concrete_model
    if not issubclass(concrete_model, Lead):
        return None

    try:
        open_value = concrete_model.Status.OPEN
    except AttributeError:
        return None

    count = concrete_model._default_manager.filter(status=open_value).count()
    label = ngettext("%(count)s open lead", "%(count)s open leads", count) % {
        "count": count
    }
    return BadgeCounterResult(primary=count, label=label)


def rfid_release_stats(badge=None):
    counts = RFID.objects.aggregate(
        total=Count("pk"),
        released_allowed=Count("pk", filter=Q(released=True, allowed=True)),
    )
    released_allowed = counts.get("released_allowed") or 0
    total = counts.get("total") or 0
    label = gettext(
        "%(released_allowed)s released and allowed RFIDs out of %(registered)s registered RFIDs"
    ) % {"released_allowed": released_allowed, "registered": total}
    return BadgeCounterResult(primary=released_allowed, secondary=total, label=label)


def charger_availability_stats(badge=None):
    available = Charger.objects.filter(last_status__iexact="Available")
    available_with_cp_number = available.filter(connector_id__isnull=False).count()

    available_without_cp_number = available.filter(connector_id__isnull=True)
    has_connector = Charger.objects.filter(
        charger_id=OuterRef("charger_id"),
        connector_id__isnull=False,
    )
    missing_connector_count = (
        available_without_cp_number.annotate(has_connector=Exists(has_connector))
        .filter(has_connector=False)
        .count()
    )

    available_total = available_with_cp_number + missing_connector_count

    if missing_connector_count:
        label = gettext(
            "%(available)s chargers reporting Available status with a CP number, out of %(total)s total Available chargers. %(missing)s Available chargers are missing a connector letter."
        ) % {
            "available": available_with_cp_number,
            "total": available_total,
            "missing": missing_connector_count,
        }
    else:
        label = gettext(
            "%(available)s chargers reporting Available status with a CP number."
        ) % {"available": available_with_cp_number}

    return BadgeCounterResult(
        primary=available_with_cp_number,
        secondary=available_total,
        label=label,
    )


def node_known_count(badge=None):
    total = Node.objects.count()
    label = ngettext(
        "%(count)s node known to this deployment",
        "%(count)s nodes known to this deployment",
        total,
    ) % {"count": total}
    return BadgeCounterResult(primary=total, label=label)
