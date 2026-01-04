import json
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from types import SimpleNamespace

from django.contrib import messages
from django.http import (Http404, HttpResponse, JsonResponse)
from django.http.request import split_domain_port
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.template.loader import render_to_string
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.utils.translation import gettext_lazy as _, gettext, ngettext
from django.utils.encoding import force_str
from django.utils.text import slugify
from django.urls import NoReverseMatch, reverse
from django.conf import settings
from django.utils import translation, timezone, formats
from django.db.utils import OperationalError, ProgrammingError
from django.core.exceptions import ValidationError
from django.db.models import (
    ExpressionWrapper,
    FloatField,
    F,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from urllib.parse import urljoin

from asgiref.sync import async_to_sync

from utils.api import api_login_required

from apps.nodes.models import NetMessage, Node
from apps.locale.models import Language
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from apps.sites.utils import landing
from apps.cards.models import RFID as CoreRFID

from django.utils.dateparse import parse_datetime

from .. import store
from ..status_resets import clear_stale_cached_statuses
from ..models import (
    Transaction,
    Charger,
    ChargerLogRequest,
    DataTransferMessage,
    ChargingProfile,
    CPReservation,
    CPFirmware,
    CPFirmwareDeployment,
    Simulator,
    annotate_transaction_energy_bounds,
)
from ..evcs import (
    _start_simulator,
    _stop_simulator,
    get_simulator_state,
)
from ..status_display import STATUS_BADGE_MAP, ERROR_OK_VALUES


CALL_ACTION_LABELS = {
    "RemoteStartTransaction": _("Remote start transaction"),
    "RemoteStopTransaction": _("Remote stop transaction"),
    "RequestStartTransaction": _("Request start transaction"),
    "RequestStopTransaction": _("Request stop transaction"),
    "GetTransactionStatus": _("Get transaction status"),
    "GetDiagnostics": _("Get diagnostics"),
    "ChangeAvailability": _("Change availability"),
    "ChangeConfiguration": _("Change configuration"),
    "DataTransfer": _("Data transfer"),
    "Reset": _("Reset"),
    "TriggerMessage": _("Trigger message"),
    "ReserveNow": _("Reserve connector"),
    "CancelReservation": _("Cancel reservation"),
    "ClearCache": _("Clear cache"),
    "UnlockConnector": _("Unlock connector"),
    "UpdateFirmware": _("Update firmware"),
    "PublishFirmware": _("Publish firmware"),
    "UnpublishFirmware": _("Unpublish firmware"),
    "SetChargingProfile": _("Set charging profile"),
    "InstallCertificate": _("Install certificate"),
    "DeleteCertificate": _("Delete certificate"),
    "CertificateSigned": _("Certificate signed"),
    "GetInstalledCertificateIds": _("Get installed certificate ids"),
    "GetVariables": _("Get variables"),
    "SetVariables": _("Set variables"),
    "ClearChargingProfile": _("Clear charging profile"),
    "SetMonitoringBase": _("Set monitoring base"),
    "SetMonitoringLevel": _("Set monitoring level"),
    "SetVariableMonitoring": _("Set variable monitoring"),
    "ClearVariableMonitoring": _("Clear variable monitoring"),
    "GetMonitoringReport": _("Get monitoring report"),
    "ClearDisplayMessage": _("Clear display message"),
    "CustomerInformation": _("Customer information"),
    "GetBaseReport": _("Get base report"),
    "GetChargingProfiles": _("Get charging profiles"),
    "GetDisplayMessages": _("Get display messages"),
    "GetReport": _("Get report"),
    "SetDisplayMessage": _("Set display message"),
    "SetNetworkProfile": _("Set network profile"),
    "GetCompositeSchedule": _("Get composite schedule"),
    "GetLocalListVersion": _("Get local list version"),
    "GetLog": _("Get log"),
}

CALL_EXPECTED_STATUSES: dict[str, set[str] | None] = {
    "RemoteStartTransaction": {"Accepted"},
    "RemoteStopTransaction": {"Accepted"},
    "RequestStartTransaction": {"Accepted"},
    "RequestStopTransaction": {"Accepted"},
    "GetDiagnostics": None,
    "ChangeAvailability": {"Accepted", "Scheduled"},
    "ChangeConfiguration": {"Accepted", "Rejected", "RebootRequired"},
    "DataTransfer": {"Accepted"},
    "Reset": {"Accepted"},
    "TriggerMessage": {"Accepted"},
    "ReserveNow": {"Accepted"},
    "CancelReservation": {"Accepted", "Rejected"},
    "ClearCache": {"Accepted", "Rejected"},
    "UnlockConnector": {"Unlocked", "Accepted"},
    "UpdateFirmware": None,
    "PublishFirmware": {"Accepted", "Rejected"},
    "UnpublishFirmware": {"Accepted", "Rejected"},
    "SetChargingProfile": {"Accepted", "Rejected", "NotSupported"},
    "InstallCertificate": {"Accepted", "Rejected"},
    "DeleteCertificate": {"Accepted", "Rejected"},
    "CertificateSigned": {"Accepted", "Rejected"},
    "GetInstalledCertificateIds": {"Accepted", "NotSupported"},
    "ClearChargingProfile": {"Accepted", "Unknown", "NotSupported"},
    "SetMonitoringBase": {"Accepted", "Rejected", "NotSupported"},
    "SetMonitoringLevel": {"Accepted", "Rejected", "NotSupported"},
    "ClearVariableMonitoring": {"Accepted", "Rejected", "NotSupported"},
    "GetMonitoringReport": {"Accepted", "Rejected", "NotSupported"},
    "ClearDisplayMessage": {"Accepted", "Unknown"},
    "CustomerInformation": {"Accepted", "Rejected", "Invalid"},
    "GetBaseReport": {"Accepted", "Rejected", "NotSupported", "EmptyResultSet"},
    "GetChargingProfiles": {"Accepted", "NoProfiles"},
    "GetDisplayMessages": {"Accepted", "Unknown"},
    "GetReport": {"Accepted", "Rejected", "NotSupported", "EmptyResultSet"},
    "SetDisplayMessage": {
        "Accepted",
        "NotSupportedMessageFormat",
        "Rejected",
        "NotSupportedPriority",
        "NotSupportedState",
        "UnknownTransaction",
    },
    "SetNetworkProfile": {"Accepted", "Rejected", "Failed"},
    "GetCompositeSchedule": {"Accepted", "Rejected"},
    "GetLocalListVersion": None,
    "GetLog": {"Accepted", "Rejected"},
}


def _clear_stale_statuses_for_view() -> None:
    """Reset cached charger state when data has gone stale."""

    clear_stale_cached_statuses()


@dataclass
class ActionCall:
    msg: str
    message_id: str
    ocpp_action: str
    expected_statuses: set[str] | None = None
    log_key: str | None = None


@dataclass
class ActionContext:
    cid: str
    connector_value: int | None
    charger: Charger | None
    ws: object
    log_key: str
    request: object | None = None


def _parse_request_body(request) -> dict:
    try:
        return json.loads(request.body.decode()) if request.body else {}
    except json.JSONDecodeError:
        return {}


def _get_or_create_charger(cid: str, connector_value: int | None) -> Charger | None:
    if connector_value is None:
        charger_obj = (
            Charger.objects.filter(charger_id=cid, connector_id__isnull=True)
            .order_by("pk")
            .first()
        )
    else:
        charger_obj = (
            Charger.objects.filter(charger_id=cid, connector_id=connector_value)
            .order_by("pk")
            .first()
        )
    if charger_obj is None:
        if connector_value is None:
            charger_obj, _created = Charger.objects.get_or_create(
                charger_id=cid, connector_id=None
            )
        else:
            charger_obj, _created = Charger.objects.get_or_create(
                charger_id=cid, connector_id=connector_value
            )
    return charger_obj

def _format_details(value: object) -> str:
    """Return a JSON representation of ``value`` suitable for error messages."""

    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
        return ""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


def _evaluate_pending_call_result(
    message_id: str,
    ocpp_action: str,
    *,
    expected_statuses: set[str] | None = None,
) -> tuple[bool, str | None, int | None]:
    """Wait for a pending call result and translate failures into messages."""

    action_label = CALL_ACTION_LABELS.get(ocpp_action, ocpp_action)
    result = store.wait_for_pending_call(message_id, timeout=5.0)
    if result is None:
        detail = _("%(action)s did not receive a response from the charger.") % {
            "action": action_label,
        }
        return False, detail, 504
    if not result.get("success", True):
        parts: list[str] = []
        error_code = str(result.get("error_code") or "").strip()
        if error_code:
            parts.append(_("code=%(code)s") % {"code": error_code})
        error_description = str(result.get("error_description") or "").strip()
        if error_description:
            parts.append(
                _("description=%(description)s") % {"description": error_description}
            )
        error_details = result.get("error_details")
        details_text = _format_details(error_details)
        if details_text:
            parts.append(_("details=%(details)s") % {"details": details_text})
        if parts:
            detail = _("%(action)s failed: %(details)s") % {
                "action": action_label,
                "details": ", ".join(parts),
            }
        else:
            detail = _("%(action)s failed.") % {"action": action_label}
        return False, detail, 400
    payload = result.get("payload")
    payload_dict = payload if isinstance(payload, dict) else {}
    if expected_statuses is not None:
        status_value = str(payload_dict.get("status") or "").strip()
        normalized_expected = {value.casefold() for value in expected_statuses if value}
        remaining = {k: v for k, v in payload_dict.items() if k != "status"}
        if not status_value:
            detail = _("%(action)s response did not include a status.") % {
                "action": action_label,
            }
            return False, detail, 400
        if normalized_expected and status_value.casefold() not in normalized_expected:
            detail = _("%(action)s rejected with status %(status)s.") % {
                "action": action_label,
                "status": status_value,
            }
            extra = _format_details(remaining)
            if extra:
                detail += " " + _("Details: %(details)s") % {"details": extra}
            return False, detail, 400
        if status_value.casefold() == "rejected":
            detail = _("%(action)s rejected with status %(status)s.") % {
                "action": action_label,
                "status": status_value,
            }
            extra = _format_details(remaining)
            if extra:
                detail += " " + _("Details: %(details)s") % {"details": extra}
            return False, detail, 400
    return True, None, None


def _normalize_connector_slug(slug: str | None) -> tuple[int | None, str]:
    """Return connector value and normalized slug or raise 404."""

    try:
        value = Charger.connector_value_from_slug(slug)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise Http404("Invalid connector") from exc
    return value, Charger.connector_slug_from_value(value)


def _reverse_connector_url(name: str, serial: str, connector_slug: str) -> str:
    """Return URL name for connector-aware routes."""

    target = f"ocpp:{name}-connector"
    if connector_slug == Charger.AGGREGATE_CONNECTOR_SLUG:
        try:
            return reverse(target, args=[serial, connector_slug])
        except NoReverseMatch:
            return reverse(f"ocpp:{name}", args=[serial])
    return reverse(target, args=[serial, connector_slug])


def _get_charger(serial: str, connector_slug: str | None) -> tuple[Charger, str]:
    """Return charger for the requested identity, creating if necessary."""

    try:
        serial = Charger.validate_serial(serial)
    except ValidationError as exc:
        raise Http404("Charger not found") from exc
    connector_value, normalized_slug = _normalize_connector_slug(connector_slug)
    if connector_value is None:
        charger, _ = Charger.objects.get_or_create(
            charger_id=serial,
            connector_id=None,
        )
    else:
        charger, _ = Charger.objects.get_or_create(
            charger_id=serial,
            connector_id=connector_value,
        )
    return charger, normalized_slug


def _connector_set(charger: Charger) -> list[Charger]:
    """Return chargers sharing the same serial ordered for navigation."""

    siblings = list(Charger.objects.filter(charger_id=charger.charger_id))
    siblings.sort(key=lambda c: (c.connector_id is not None, c.connector_id or 0))
    return siblings


def _visible_error_code(value: str | None) -> str | None:
    """Return ``value`` when it represents a real error code."""

    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.lower() in ERROR_OK_VALUES:
        return None
    return normalized


def _visible_chargers(user):
    """Return chargers visible to ``user`` on public dashboards."""

    return Charger.visible_for_user(user).prefetch_related("owner_users", "owner_groups")


def _charger_last_seen(charger: Charger | object):
    """Return the last activity timestamp for ``charger`` safely.

    Some environments may serve an older charger model that lacks the
    ``last_seen`` helper property. Access the field defensively and fall back to
    known status fields so the dashboard rendering does not crash.
    """

    try:
        last_seen = getattr(charger, "last_seen")
    except AttributeError:
        last_seen = None
    if last_seen is None:
        last_seen = getattr(charger, "last_status_timestamp", None) or getattr(
            charger, "last_heartbeat", None
        )
    return last_seen


def _ensure_charger_access(
    user,
    charger: Charger,
    *,
    request=None,
) -> HttpResponse | None:
    """Ensure ``user`` may view ``charger``.

    Returns a redirect to the login page when authentication is required,
    otherwise raises :class:`~django.http.Http404` if the charger should not be
    visible to the user.
    """

    if charger.is_visible_to(user):
        return None
    if (
        request is not None
        and not getattr(user, "is_authenticated", False)
        and charger.has_owner_scope()
    ):
        return redirect_to_login(
            request.get_full_path(),
            login_url=resolve_url(settings.LOGIN_URL),
        )
    raise Http404("Charger not found")


def _transaction_rfid_details(
    tx_obj, *, cache: dict[str, dict[str, str | None]] | None = None
) -> dict[str, str | None] | None:
    """Return normalized RFID metadata for a transaction-like object."""

    if not tx_obj:
        return None
    rfid_value = getattr(tx_obj, "rfid", None)
    normalized = str(rfid_value or "").strip().upper()
    cache_key = normalized
    if normalized:
        if cache is not None and cache_key in cache:
            return cache[cache_key]
        tag = (
            CoreRFID.matching_queryset(normalized)
            .only("pk", "label_id", "custom_label")
            .first()
        )
        rfid_url = None
        label_value = None
        canonical_value = normalized
        if tag:
            try:
                rfid_url = reverse("admin:cards_rfid_change", args=[tag.pk])
            except NoReverseMatch:  # pragma: no cover - admin may be disabled
                rfid_url = None
            custom_label = (tag.custom_label or "").strip()
            if custom_label:
                label_value = custom_label
            elif tag.label_id is not None:
                label_value = str(tag.label_id)
            canonical_value = tag.rfid or canonical_value
        display_value = label_value or canonical_value
        details = {
            "value": display_value,
            "url": rfid_url,
            "uid": canonical_value,
            "type": "rfid",
            "display_label": gettext("RFID"),
        }
        if label_value:
            details["label"] = label_value
        if cache is not None:
            cache[cache_key] = details
        return details

    identifier_value = getattr(tx_obj, "vehicle_identifier", None)
    normalized_identifier = str(identifier_value or "").strip()
    if not normalized_identifier:
        vid_value = getattr(tx_obj, "vid", None)
        vin_value = getattr(tx_obj, "vin", None)
        normalized_identifier = str(vid_value or vin_value or "").strip()
    if not normalized_identifier:
        return None
    source = getattr(tx_obj, "vehicle_identifier_source", "") or "vid"
    if source not in {"vid", "vin"}:
        vid_raw = getattr(tx_obj, "vid", None)
        vin_raw = getattr(tx_obj, "vin", None)
        if str(vid_raw or "").strip():
            source = "vid"
        elif str(vin_raw or "").strip():
            source = "vin"
        else:
            source = "vid"
    cache_key = f"{source}:{normalized_identifier}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    label = gettext("VID") if source == "vid" else gettext("VIN")
    details = {
        "value": normalized_identifier,
        "url": None,
        "uid": None,
        "type": source,
        "display_label": label,
    }
    if cache is not None:
        cache[cache_key] = details
    return details


def _connector_overview(
    charger: Charger,
    user=None,
    *,
    connectors: list[Charger] | None = None,
    rfid_cache: dict[str, dict[str, str | None]] | None = None,
) -> list[dict]:
    """Return connector metadata used for navigation and summaries."""

    overview: list[dict] = []
    sibling_connectors = connectors if connectors is not None else _connector_set(charger)
    for sibling in sibling_connectors:
        if user is not None and not sibling.is_visible_to(user):
            continue
        tx_obj = store.get_transaction(sibling.charger_id, sibling.connector_id)
        state, color = _charger_state(sibling, tx_obj)
        overview.append(
            {
                "charger": sibling,
                "slug": sibling.connector_slug,
                "label": sibling.connector_label,
                "url": _reverse_connector_url(
                    "charger-page", sibling.charger_id, sibling.connector_slug
                ),
                "status": state,
                "color": color,
                "last_status": sibling.last_status,
                "last_error_code": _visible_error_code(sibling.last_error_code),
                "last_status_timestamp": sibling.last_status_timestamp,
                "last_status_vendor_info": sibling.last_status_vendor_info,
                "tx": tx_obj,
                "rfid_details": _transaction_rfid_details(
                    tx_obj, cache=rfid_cache
                ),
                "connected": store.is_connected(
                    sibling.charger_id, sibling.connector_id
                ),
            }
        )
    return overview


def _normalize_timeline_status(value: str | None) -> str | None:
    """Normalize raw charger status strings into timeline buckets."""

    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    charging_states = {
        "charging",
        "finishing",
        "suspendedev",
        "suspendedevse",
        "occupied",
    }
    available_states = {"available", "preparing", "reserved"}
    offline_states = {"faulted", "unavailable", "outofservice"}
    if normalized in charging_states:
        return "charging"
    if normalized in offline_states:
        return "offline"
    if normalized in available_states:
        return "available"
    # Treat other states as available for the initial implementation.
    return "available"


def _timeline_labels() -> dict[str, str]:
    """Return translated labels for timeline statuses."""

    return {
        "offline": gettext("Offline"),
        "untracked": gettext("Untracked"),
        "available": gettext("Available"),
        "charging": gettext("Charging"),
    }


def _remote_node_active_delta() -> timedelta:
    """Return the grace period for considering a remote node online."""

    value = getattr(settings, "NODE_LAST_SEEN_ACTIVE_DELTA", None)
    if isinstance(value, timedelta):
        return value
    if value is None:
        return timedelta(minutes=5)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return timedelta(minutes=5)
    return timedelta(seconds=seconds)


def _is_untracked_origin(
    connector: Charger,
    local_node: Node | None,
    reference_time: datetime,
    active_delta: timedelta,
) -> bool:
    """Return ``True`` when the connector's node origin appears offline."""

    origin = getattr(connector, "node_origin", None)
    if origin is None:
        return False
    local_pk = getattr(local_node, "pk", None)
    if local_pk is not None and origin.pk == local_pk:
        return False
    last_seen = getattr(origin, "last_seen", None)
    if last_seen is None:
        return True
    if timezone.is_naive(last_seen):
        last_seen = timezone.make_aware(last_seen, timezone.get_current_timezone())
    return reference_time - last_seen > active_delta


def _format_segment_range(start: datetime, end: datetime) -> tuple[str, str]:
    """Return localized display values for a timeline range."""

    start_display = formats.date_format(
        timezone.localtime(start), "SHORT_DATETIME_FORMAT"
    )
    end_display = formats.date_format(timezone.localtime(end), "SHORT_DATETIME_FORMAT")
    return start_display, end_display


def _collect_status_events(
    charger: Charger,
    connector: Charger,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[tuple[datetime, str]], tuple[datetime, str] | None]:
    """Parse log entries into ordered status events for the connector."""

    connector_id = connector.connector_id
    serial = connector.charger_id
    keys = [store.identity_key(serial, connector_id)]
    if connector_id is not None:
        keys.append(store.identity_key(serial, None))
        keys.append(store.pending_key(serial))

    events: list[tuple[datetime, str]] = []
    latest_before_window: tuple[datetime, str] | None = None

    for entry in store.iter_log_entries(keys, log_type="charger", since=window_start):
        if len(entry.text) < 24:
            continue
        message = entry.text[24:].strip()
        log_timestamp = entry.timestamp

        event_time = log_timestamp
        status_bucket: str | None = None

        if message.startswith("StatusNotification processed:"):
            payload_text = message.split(":", 1)[1].strip()
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            target_id = payload.get("connectorId")
            if connector_id is not None:
                try:
                    normalized_target = int(target_id)
                except (TypeError, ValueError):
                    normalized_target = None
                if normalized_target not in {connector_id, None}:
                    continue
            raw_status = payload.get("status")
            status_bucket = _normalize_timeline_status(
                raw_status if isinstance(raw_status, str) else None
            )
            payload_timestamp = payload.get("timestamp")
            if isinstance(payload_timestamp, str):
                parsed = parse_datetime(payload_timestamp)
                if parsed is not None:
                    if timezone.is_naive(parsed):
                        parsed = timezone.make_aware(parsed, timezone=dt_timezone.utc)
                    event_time = parsed
        elif message.startswith("Connected"):
            status_bucket = "available"
        elif message.startswith("Closed"):
            status_bucket = "offline"

        if not status_bucket:
            continue

        if event_time < window_start:
            if (
                latest_before_window is None
                or event_time > latest_before_window[0]
            ):
                latest_before_window = (event_time, status_bucket)
            break
        if event_time > window_end:
            continue
        events.append((event_time, status_bucket))

    events.sort(key=lambda item: item[0])

    deduped_events: list[tuple[datetime, str]] = []
    for event_time, state in events:
        if deduped_events and deduped_events[-1][1] == state:
            continue
        deduped_events.append((event_time, state))

    return deduped_events, latest_before_window


def _usage_timeline(
    charger: Charger,
    connector_overview: list[dict],
    *,
    now: datetime | None = None,
) -> tuple[list[dict], tuple[str, str] | None]:
    """Build usage timeline data for inactive chargers."""

    if now is None:
        now = timezone.now()
    window_end = now
    window_start = now - timedelta(days=7)
    local_node = Node.get_local()
    active_delta = _remote_node_active_delta()

    if charger.connector_id is not None:
        connectors = [charger]
    else:
        connectors = [
            item["charger"]
            for item in connector_overview
            if item.get("charger") and item["charger"].connector_id is not None
        ]
        if not connectors:
            connectors = [
                sibling
                for sibling in _connector_set(charger)
                if sibling.connector_id is not None
            ]

    seen_ids: set[int] = set()
    labels = _timeline_labels()
    timeline_entries: list[dict] = []
    window_display: tuple[str, str] | None = None

    if window_start < window_end:
        window_display = _format_segment_range(window_start, window_end)

    for connector in connectors:
        if connector.connector_id is None:
            continue
        if connector.connector_id in seen_ids:
            continue
        seen_ids.add(connector.connector_id)

        events, prior_event = _collect_status_events(
            charger, connector, window_start, window_end
        )
        fallback_state = _normalize_timeline_status(connector.last_status)
        fallback_source = "status"
        if fallback_state is None:
            fallback_source = "connection"
            fallback_state = (
                "available"
                if store.is_connected(connector.charger_id, connector.connector_id)
                else "offline"
            )
        if (
            fallback_state == "offline"
            and fallback_source == "connection"
            and _is_untracked_origin(
                connector, local_node, window_end, active_delta
            )
        ):
            fallback_state = "untracked"
        current_state = fallback_state
        if prior_event is not None:
            current_state = prior_event[1]
        segments: list[dict] = []
        previous_time = window_start
        total_seconds = (window_end - window_start).total_seconds()

        for event_time, state in events:
            if event_time <= window_start:
                current_state = state
                continue
            if event_time > window_end:
                break
            if state == current_state:
                continue
            segment_start = max(previous_time, window_start)
            segment_end = min(event_time, window_end)
            if segment_end > segment_start:
                duration = (segment_end - segment_start).total_seconds()
                start_display, end_display = _format_segment_range(
                    segment_start, segment_end
                )
                segments.append(
                    {
                        "status": current_state,
                        "label": labels.get(current_state, current_state.title()),
                        "start_display": start_display,
                        "end_display": end_display,
                        "duration": max(duration, 1.0),
                    }
                )
            current_state = state
            previous_time = max(event_time, window_start)

        if previous_time < window_end:
            segment_start = max(previous_time, window_start)
            segment_end = window_end
            if segment_end > segment_start:
                duration = (segment_end - segment_start).total_seconds()
                start_display, end_display = _format_segment_range(
                    segment_start, segment_end
                )
                segments.append(
                    {
                        "status": current_state,
                        "label": labels.get(current_state, current_state.title()),
                        "start_display": start_display,
                        "end_display": end_display,
                        "duration": max(duration, 1.0),
                    }
                )

        if not segments and total_seconds > 0:
            start_display, end_display = _format_segment_range(window_start, window_end)
            segments.append(
                {
                    "status": current_state,
                    "label": labels.get(current_state, current_state.title()),
                    "start_display": start_display,
                    "end_display": end_display,
                    "duration": max(total_seconds, 1.0),
                }
            )

        if segments:
            timeline_entries.append(
                {
                    "label": connector.connector_label,
                    "segments": segments,
                }
            )

    return timeline_entries, window_display


def _live_sessions(
    charger: Charger, *, connectors: list[Charger] | None = None
) -> list[tuple[Charger, Transaction]]:
    """Return active sessions grouped by connector for the charger."""

    siblings = connectors if connectors is not None else _connector_set(charger)
    ordered = [c for c in siblings if c.connector_id is not None] + [
        c for c in siblings if c.connector_id is None
    ]
    sessions: list[tuple[Charger, Transaction]] = []
    seen: set[int] = set()
    for sibling in ordered:
        tx_obj = store.get_transaction(sibling.charger_id, sibling.connector_id)
        if not tx_obj:
            continue
        if tx_obj.pk and tx_obj.pk in seen:
            continue
        if tx_obj.pk:
            seen.add(tx_obj.pk)
        sessions.append((sibling, tx_obj))
    return sessions


def _supported_language_codes() -> list[str]:
    codes: list[str] = []
    try:
        codes = [
            str(code).strip()
            for code in Language.objects.filter(is_deleted=False)
            .values_list("code", flat=True)
            if str(code).strip()
        ]
    except (OperationalError, ProgrammingError):
        codes = []

    if codes:
        return codes

    return [
        str(code).strip()
        for code, _ in getattr(settings, "LANGUAGES", [])
        if str(code).strip()
    ]


def _default_language_code() -> str:
    try:
        code = (
            Language.objects.filter(is_deleted=False, is_default=True)
            .values_list("code", flat=True)
            .first()
            or ""
        )
    except (OperationalError, ProgrammingError):
        code = ""

    normalized = str(code).strip()
    if normalized:
        return normalized

    configured = str(getattr(settings, "LANGUAGE_CODE", "") or "").strip()
    if configured:
        base = configured.replace("_", "-")
        parts = base.split("-", 1)
        return parts[0] if parts else base

    supported = _supported_language_codes()
    return supported[0] if supported else ""


@lru_cache(maxsize=1)
def _landing_page_translations() -> dict[str, dict[str, str]]:
    """Return static translations used by the charger public landing page."""

    catalog: dict[str, dict[str, str]] = {}
    seen_codes: set[str] = set()
    for code in _supported_language_codes():
        normalized = str(code).strip()
        if not normalized or normalized in seen_codes:
            continue
        seen_codes.add(normalized)
        with translation.override(normalized):
            catalog[normalized] = {
                "serial_number_label": gettext("Serial Number"),
                "connector_label": gettext("Connector"),
                "advanced_view_label": gettext("Advanced View"),
                "require_rfid_label": gettext("Require RFID Authorization"),
                "charging_label": gettext("Charging"),
                "energy_label": gettext("Energy"),
                "started_label": gettext("Started"),
                "rfid_label": gettext("RFID"),
                "instruction_text": gettext(
                    "Plug in your vehicle and slide your RFID card over the reader to begin charging."
                ),
                "connectors_heading": gettext("Connectors"),
                "no_active_transaction": gettext("No active transaction"),
                "connectors_active_singular": ngettext(
                    "%(count)s connector active",
                    "%(count)s connectors active",
                    1,
                ),
                "connectors_active_plural": ngettext(
                    "%(count)s connector active",
                    "%(count)s connectors active",
                    2,
                ),
                "status_reported_label": gettext("Reported status"),
                "status_error_label": gettext("Error code"),
                "status_updated_label": gettext("Last status update"),
                "status_vendor_label": gettext("Vendor"),
                "status_info_label": gettext("Info"),
                "latest_status_label": gettext("Latest report"),
            }
    return catalog


def _has_active_session(tx_obj) -> bool:
    """Return whether the provided transaction-like object is active."""

    if isinstance(tx_obj, (list, tuple, set)):
        return any(_has_active_session(item) for item in tx_obj)
    if not tx_obj:
        return False
    if isinstance(tx_obj, dict):
        return tx_obj.get("stop_time") is None
    stop_time = getattr(tx_obj, "stop_time", None)
    return stop_time is None


def _aggregate_dashboard_state(charger: Charger) -> tuple[str, str] | None:
    """Return an aggregate badge for the charger when summarising connectors."""

    if charger.connector_id is not None:
        return None

    siblings = (
        Charger.objects.filter(charger_id=charger.charger_id)
        .exclude(pk=charger.pk)
        .exclude(connector_id__isnull=True)
    )
    statuses: list[str] = []
    for sibling in siblings:
        tx_obj = store.get_transaction(sibling.charger_id, sibling.connector_id)
        if not tx_obj:
            tx_obj = (
                Transaction.objects.filter(charger=sibling, stop_time__isnull=True)
                .order_by("-start_time")
                .first()
            )
        has_session = _has_active_session(tx_obj)
        status_value = (sibling.last_status or "").strip()
        normalized_status = status_value.casefold() if status_value else ""
        error_code_lower = (sibling.last_error_code or "").strip().lower()
        if has_session:
            statuses.append("charging")
            continue
        if (
            normalized_status in {"charging", "finishing"}
            and error_code_lower in ERROR_OK_VALUES
        ):
            statuses.append("available")
            continue
        if normalized_status:
            statuses.append(normalized_status)
            continue
        if store.is_connected(sibling.charger_id, sibling.connector_id):
            statuses.append("available")

    if not statuses:
        return None

    if any(status == "available" for status in statuses):
        return STATUS_BADGE_MAP["available"]

    if all(status == "charging" for status in statuses):
        return STATUS_BADGE_MAP["charging"]

    return None


def _charger_state(charger: Charger, tx_obj: Transaction | list | None):
    """Return human readable state and color for a charger."""

    status_value = (charger.last_status or "").strip()
    normalized_status = status_value.casefold() if status_value else ""

    aggregate_state = _aggregate_dashboard_state(charger)
    if aggregate_state is not None and normalized_status in {"", "available", "charging"}:
        return aggregate_state

    has_session = _has_active_session(tx_obj)
    if status_value:
        key = normalized_status
        label, color = STATUS_BADGE_MAP.get(key, (status_value, "#0d6efd"))
        error_code = (charger.last_error_code or "").strip()
        error_code_lower = error_code.lower()
        if (
            has_session
            and error_code_lower in ERROR_OK_VALUES
            and (key not in STATUS_BADGE_MAP or key == "available")
        ):
            # Some stations continue reporting "Available" (or an unknown status)
            # while a session is active. Override the badge so the user can see
            # the charger is actually busy.
            label, color = STATUS_BADGE_MAP.get("charging", (_("Charging"), "#198754"))
        elif (
            not has_session
            and key in {"charging", "finishing"}
            and error_code_lower in ERROR_OK_VALUES
        ):
            # Some chargers continue reporting "Charging" after a session ends.
            # When no active transaction exists, surface the state as available
            # so the UI reflects the actual behaviour at the site.
            label, color = STATUS_BADGE_MAP.get("available", (_("Available"), "#0d6efd"))
        elif error_code and error_code_lower not in ERROR_OK_VALUES:
            label = _("%(status)s (%(error)s)") % {
                "status": label,
                "error": error_code,
            }
            color = "#dc3545"
        return label, color

    cid = charger.charger_id
    connected = store.is_connected(cid, charger.connector_id)
    if connected and has_session:
        return _("Charging"), "green"
    if connected:
        return _("Available"), "blue"
    return _("Offline"), "grey"


def _diagnostics_payload(charger: Charger) -> dict[str, str | None]:
    """Return diagnostics metadata for API responses."""

    timestamp = (
        charger.diagnostics_timestamp.isoformat()
        if charger.diagnostics_timestamp
        else None
    )
    status = charger.diagnostics_status or None
    location = charger.diagnostics_location or None
    return {
        "diagnosticsStatus": status,
        "diagnosticsTimestamp": timestamp,
        "diagnosticsLocation": location,
    }


def _charging_limit_details(charger: Charger) -> dict[str, object] | None:
    """Return structured charging limit details for dashboard views."""

    payload = charger.last_charging_limit or {}
    limit_data = payload.get("chargingLimit") if isinstance(payload, dict) else {}
    if not isinstance(limit_data, dict):
        limit_data = {}
    source = (charger.last_charging_limit_source or "").strip() or (
        str(limit_data.get("chargingLimitSource") or "").strip()
    )
    grid_critical = charger.last_charging_limit_is_grid_critical
    if grid_critical is None:
        flag = limit_data.get("isGridCritical")
        grid_critical = bool(flag) if flag is not None else None

    evse_value = None
    if isinstance(payload, dict):
        evse_value = payload.get("evseId")
    try:
        evse_id = int(evse_value) if evse_value is not None else None
    except (TypeError, ValueError):
        evse_id = None

    raw_schedules = []
    if isinstance(payload, dict):
        raw_schedules = payload.get("chargingSchedule") or []
    schedules = raw_schedules if isinstance(raw_schedules, list) else []

    if not (source or schedules or grid_critical or evse_id is not None):
        return None

    summaries: list[str] = []
    for schedule in schedules:
        if not isinstance(schedule, dict):
            continue
        unit = schedule.get("chargingRateUnit") or ""
        periods = schedule.get("chargingSchedulePeriod") or []
        limit_value: Decimal | None = None
        for period in periods:
            if not isinstance(period, dict):
                continue
            try:
                limit_value = Decimal(str(period.get("limit")))
                break
            except (InvalidOperation, TypeError, ValueError):
                continue
        parts: list[str] = []
        if limit_value is not None:
            parts.append(f"{limit_value.normalize()} {unit}".strip())
        elif unit:
            parts.append(unit)
        duration = schedule.get("duration")
        if duration:
            parts.append(_("duration %(seconds)s s") % {"seconds": duration})
        if not parts:
            parts.append(_("charging schedule"))
        summaries.append(", ".join(parts))

    label_parts: list[str] = []
    if source:
        label_parts.append(source)
    if grid_critical is True:
        label_parts.append(_("grid critical"))
    elif grid_critical is False:
        label_parts.append(_("grid stable"))
    if summaries:
        label_parts.append(summaries[0])
    elif evse_id is not None:
        label_parts.append(_("EVSE %(evse)s") % {"evse": evse_id})

    label = ", ".join(str(part) for part in label_parts) if label_parts else None

    return {
        "source": source or None,
        "evse_id": evse_id,
        "is_grid_critical": grid_critical,
        "schedules": summaries,
        "schedule_count": len(summaries),
        "label": label,
        "timestamp": charger.last_charging_limit_at,
    }
