import json
import uuid
from datetime import datetime, time, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.http import Http404, HttpResponse, JsonResponse
from django.http.request import split_domain_port
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, render, resolve_url
from django.template.loader import render_to_string
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.utils.translation import gettext_lazy as _, gettext, ngettext
from django.utils.text import slugify
from django.urls import NoReverseMatch, reverse
from django.conf import settings
from django.utils import translation, timezone, formats
from django.core.exceptions import ValidationError

from asgiref.sync import async_to_sync

from utils.api import api_login_required

from nodes.models import Node

from pages.utils import landing
from core.liveupdate import live_update

from django.utils.dateparse import parse_datetime

from . import store
from .models import (
    Transaction,
    Charger,
    DataTransferMessage,
    RFID,
    CPFirmwareDeployment,
)
from .evcs import (
    _start_simulator,
    _stop_simulator,
    get_simulator_state,
    _simulator_status_json,
)
from .status_display import STATUS_BADGE_MAP, ERROR_OK_VALUES


CALL_ACTION_LABELS = {
    "RemoteStartTransaction": _("Remote start transaction"),
    "RemoteStopTransaction": _("Remote stop transaction"),
    "ChangeAvailability": _("Change availability"),
    "ChangeConfiguration": _("Change configuration"),
    "DataTransfer": _("Data transfer"),
    "Reset": _("Reset"),
    "TriggerMessage": _("Trigger message"),
    "ReserveNow": _("Reserve connector"),
    "ClearCache": _("Clear cache"),
}

CALL_EXPECTED_STATUSES: dict[str, set[str]] = {
    "RemoteStartTransaction": {"Accepted"},
    "RemoteStopTransaction": {"Accepted"},
    "ChangeAvailability": {"Accepted", "Scheduled"},
    "ChangeConfiguration": {"Accepted", "Rejected", "RebootRequired"},
    "DataTransfer": {"Accepted"},
    "Reset": {"Accepted"},
    "TriggerMessage": {"Accepted"},
    "ReserveNow": {"Accepted"},
    "ClearCache": {"Accepted", "Rejected"},
}


def firmware_download(request, deployment_id: int, token: str):
    deployment = get_object_or_404(
        CPFirmwareDeployment,
        pk=deployment_id,
        download_token=token,
    )
    expires = deployment.download_token_expires_at
    if expires and timezone.now() > expires:
        return HttpResponse(status=403)
    firmware = deployment.firmware
    if firmware is None:
        raise Http404
    payload = firmware.get_payload_bytes()
    if not payload:
        raise Http404
    content_type = firmware.content_type or "application/octet-stream"
    response = HttpResponse(payload, content_type=content_type)
    filename = firmware.filename or f"firmware_{firmware.pk or deployment.pk}"
    safe_filename = filename.replace("\r", "").replace("\n", "").replace("\"", "")
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    response["Content-Length"] = str(len(payload))
    deployment.downloaded_at = timezone.now()
    deployment.save(update_fields=["downloaded_at", "updated_at"])
    return response


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

    target = f"{name}-connector"
    if connector_slug == Charger.AGGREGATE_CONNECTOR_SLUG:
        try:
            return reverse(target, args=[serial, connector_slug])
        except NoReverseMatch:
            return reverse(name, args=[serial])
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
            RFID.matching_queryset(normalized)
            .only("pk", "label_id", "custom_label")
            .first()
        )
        rfid_url = None
        label_value = None
        canonical_value = normalized
        if tag:
            try:
                rfid_url = reverse("admin:core_rfid_change", args=[tag.pk])
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
    rfid_cache: dict[str, dict[str, str | None]] | None = None,
) -> list[dict]:
    """Return connector metadata used for navigation and summaries."""

    overview: list[dict] = []
    for sibling in _connector_set(charger):
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
        "available": gettext("Available"),
        "charging": gettext("Charging"),
    }


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
        if fallback_state is None:
            fallback_state = (
                "available"
                if store.is_connected(connector.charger_id, connector.connector_id)
                else "offline"
            )
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


def _live_sessions(charger: Charger) -> list[tuple[Charger, Transaction]]:
    """Return active sessions grouped by connector for the charger."""

    siblings = _connector_set(charger)
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


def _landing_page_translations() -> dict[str, dict[str, str]]:
    """Return static translations used by the charger public landing page."""

    catalog: dict[str, dict[str, str]] = {}
    seen_codes: set[str] = set()
    for code, _name in settings.LANGUAGES:
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


@api_login_required
def charger_list(request):
    """Return a JSON list of known chargers and state."""
    data = []
    for charger in _visible_chargers(request.user):
        cid = charger.charger_id
        sessions: list[tuple[Charger, Transaction]] = []
        tx_obj = store.get_transaction(cid, charger.connector_id)
        if charger.connector_id is None:
            sessions = _live_sessions(charger)
            if sessions:
                tx_obj = sessions[0][1]
        elif tx_obj:
            sessions = [(charger, tx_obj)]
        if not tx_obj:
            tx_obj = (
                Transaction.objects.filter(charger__charger_id=cid)
                .order_by("-start_time")
                .first()
            )
        tx_data = None
        if tx_obj:
            tx_data = {
                "transactionId": tx_obj.pk,
                "meterStart": tx_obj.meter_start,
                "startTime": tx_obj.start_time.isoformat(),
            }
            identifier = str(getattr(tx_obj, "vehicle_identifier", "") or "").strip()
            if identifier:
                tx_data["vid"] = identifier
            legacy_vin = str(getattr(tx_obj, "vin", "") or "").strip()
            if legacy_vin:
                tx_data["vin"] = legacy_vin
            if tx_obj.meter_stop is not None:
                tx_data["meterStop"] = tx_obj.meter_stop
            if tx_obj.stop_time is not None:
                tx_data["stopTime"] = tx_obj.stop_time.isoformat()
        active_transactions = []
        for session_charger, session_tx in sessions:
            active_payload = {
                "charger_id": session_charger.charger_id,
                "connector_id": session_charger.connector_id,
                "connector_slug": session_charger.connector_slug,
                "transactionId": session_tx.pk,
                "meterStart": session_tx.meter_start,
                "startTime": session_tx.start_time.isoformat(),
            }
            identifier = str(getattr(session_tx, "vehicle_identifier", "") or "").strip()
            if identifier:
                active_payload["vid"] = identifier
            legacy_vin = str(getattr(session_tx, "vin", "") or "").strip()
            if legacy_vin:
                active_payload["vin"] = legacy_vin
            if session_tx.meter_stop is not None:
                active_payload["meterStop"] = session_tx.meter_stop
            if session_tx.stop_time is not None:
                active_payload["stopTime"] = session_tx.stop_time.isoformat()
            active_transactions.append(active_payload)
        state, color = _charger_state(
            charger,
            tx_obj if charger.connector_id is not None else (sessions if sessions else None),
        )
        entry = {
            "charger_id": cid,
            "name": charger.name,
            "connector_id": charger.connector_id,
            "connector_slug": charger.connector_slug,
            "connector_label": charger.connector_label,
            "require_rfid": charger.require_rfid,
            "transaction": tx_data,
            "activeTransactions": active_transactions,
            "lastHeartbeat": (
                charger.last_heartbeat.isoformat()
                if charger.last_heartbeat
                else None
            ),
            "lastMeterValues": charger.last_meter_values,
            "firmwareStatus": charger.firmware_status,
            "firmwareStatusInfo": charger.firmware_status_info,
            "firmwareTimestamp": (
                charger.firmware_timestamp.isoformat()
                if charger.firmware_timestamp
                else None
            ),
            "connected": store.is_connected(cid, charger.connector_id),
            "lastStatus": charger.last_status or None,
            "lastErrorCode": charger.last_error_code or None,
            "lastStatusTimestamp": (
                charger.last_status_timestamp.isoformat()
                if charger.last_status_timestamp
                else None
            ),
            "lastStatusVendorInfo": charger.last_status_vendor_info,
            "status": state,
            "statusColor": color,
        }
        entry.update(_diagnostics_payload(charger))
        data.append(entry)
    return JsonResponse({"chargers": data})


@api_login_required
def charger_detail(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(
        request.user, charger, request=request
    )
    if access_response is not None:
        return access_response

    sessions: list[tuple[Charger, Transaction]] = []
    tx_obj = store.get_transaction(cid, charger.connector_id)
    if charger.connector_id is None:
        sessions = _live_sessions(charger)
        if sessions:
            tx_obj = sessions[0][1]
    elif tx_obj:
        sessions = [(charger, tx_obj)]
    if not tx_obj:
        tx_obj = (
            Transaction.objects.filter(charger__charger_id=cid)
            .order_by("-start_time")
            .first()
        )

    tx_data = None
    if tx_obj:
        tx_data = {
            "transactionId": tx_obj.pk,
            "meterStart": tx_obj.meter_start,
            "startTime": tx_obj.start_time.isoformat(),
        }
        identifier = str(getattr(tx_obj, "vehicle_identifier", "") or "").strip()
        if identifier:
            tx_data["vid"] = identifier
        legacy_vin = str(getattr(tx_obj, "vin", "") or "").strip()
        if legacy_vin:
            tx_data["vin"] = legacy_vin
        if tx_obj.meter_stop is not None:
            tx_data["meterStop"] = tx_obj.meter_stop
        if tx_obj.stop_time is not None:
            tx_data["stopTime"] = tx_obj.stop_time.isoformat()

    active_transactions = []
    for session_charger, session_tx in sessions:
        payload = {
            "charger_id": session_charger.charger_id,
            "connector_id": session_charger.connector_id,
            "connector_slug": session_charger.connector_slug,
            "transactionId": session_tx.pk,
            "meterStart": session_tx.meter_start,
            "startTime": session_tx.start_time.isoformat(),
        }
        identifier = str(getattr(session_tx, "vehicle_identifier", "") or "").strip()
        if identifier:
            payload["vid"] = identifier
        legacy_vin = str(getattr(session_tx, "vin", "") or "").strip()
        if legacy_vin:
            payload["vin"] = legacy_vin
        if session_tx.meter_stop is not None:
            payload["meterStop"] = session_tx.meter_stop
        if session_tx.stop_time is not None:
            payload["stopTime"] = session_tx.stop_time.isoformat()
        active_transactions.append(payload)

    log_key = store.identity_key(cid, charger.connector_id)
    log = store.get_logs(log_key, log_type="charger")
    state, color = _charger_state(
        charger,
        tx_obj if charger.connector_id is not None else (sessions if sessions else None),
    )
    payload = {
        "charger_id": cid,
        "connector_id": charger.connector_id,
        "connector_slug": connector_slug,
        "name": charger.name,
        "require_rfid": charger.require_rfid,
        "transaction": tx_data,
        "activeTransactions": active_transactions,
        "lastHeartbeat": (
            charger.last_heartbeat.isoformat() if charger.last_heartbeat else None
        ),
        "lastMeterValues": charger.last_meter_values,
        "firmwareStatus": charger.firmware_status,
        "firmwareStatusInfo": charger.firmware_status_info,
        "firmwareTimestamp": (
            charger.firmware_timestamp.isoformat()
            if charger.firmware_timestamp
            else None
        ),
        "log": log,
        "lastStatus": charger.last_status or None,
        "lastErrorCode": charger.last_error_code or None,
        "lastStatusTimestamp": (
            charger.last_status_timestamp.isoformat()
            if charger.last_status_timestamp
            else None
        ),
        "lastStatusVendorInfo": charger.last_status_vendor_info,
        "status": state,
        "statusColor": color,
    }
    payload.update(_diagnostics_payload(charger))
    return JsonResponse(payload)


@landing("CPMS Online Dashboard")
@live_update()
def dashboard(request):
    """Landing page listing all known chargers and their status."""
    node = Node.get_local()
    role = node.role if node else None
    role_name = role.name if role else ""
    allow_anonymous_roles = {"Watchtower", "Constellation", "Satellite"}
    if not request.user.is_authenticated and role_name not in allow_anonymous_roles:
        return redirect_to_login(
            request.get_full_path(), login_url=reverse("pages:login")
        )
    is_watchtower = role_name in {"Watchtower", "Constellation"}
    visible_chargers = (
        _visible_chargers(request.user)
        .select_related("location")
        .order_by("charger_id", "connector_id")
    )
    stats_cache: dict[int, dict[str, float]] = {}

    def _charger_display_name(charger: Charger) -> str:
        if charger.display_name:
            return charger.display_name
        if charger.location:
            return charger.location.name
        return charger.charger_id

    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    day_start = datetime.combine(today, time.min)
    if timezone.is_naive(day_start):
        day_start = timezone.make_aware(day_start, tz)
    day_end = day_start + timedelta(days=1)

    def _charger_stats(charger: Charger) -> dict[str, float]:
        cache_key = charger.pk or id(charger)
        if cache_key not in stats_cache:
            stats_cache[cache_key] = {
                "total_kw": charger.total_kw,
                "today_kw": charger.total_kw_for_range(day_start, day_end),
            }
        return stats_cache[cache_key]

    def _status_url(charger: Charger) -> str:
        return _reverse_connector_url(
            "charger-status",
            charger.charger_id,
            charger.connector_slug,
        )

    chargers: list[dict[str, object]] = []
    charger_groups: list[dict[str, object]] = []
    group_lookup: dict[str, dict[str, object]] = {}

    for charger in visible_chargers:
        tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
        if not tx_obj:
            tx_obj = (
                Transaction.objects.filter(charger=charger)
                .order_by("-start_time")
                .first()
            )
        has_session = _has_active_session(tx_obj)
        state, color = _charger_state(charger, tx_obj)
        if (
            charger.connector_id is not None
            and not has_session
            and (charger.last_status or "").strip().casefold() == "charging"
        ):
            state, color = STATUS_BADGE_MAP["charging"]
        entry = {
            "charger": charger,
            "state": state,
            "color": color,
            "display_name": _charger_display_name(charger),
            "stats": _charger_stats(charger),
            "status_url": _status_url(charger),
        }
        chargers.append(entry)
        if charger.connector_id is None:
            group = {"parent": entry, "children": []}
            charger_groups.append(group)
            group_lookup[charger.charger_id] = group
        else:
            group = group_lookup.get(charger.charger_id)
            if group is None:
                group = {"parent": None, "children": []}
                charger_groups.append(group)
                group_lookup[charger.charger_id] = group
            group["children"].append(entry)

    for group in charger_groups:
        parent_entry = group.get("parent")
        if not parent_entry or not group["children"]:
            continue
        connector_statuses = [
            (child["charger"].last_status or "").strip().casefold()
            for child in group["children"]
            if child["charger"].connector_id is not None
        ]
        if connector_statuses and all(status == "charging" for status in connector_statuses):
            label, badge_color = STATUS_BADGE_MAP["charging"]
            parent_entry["state"] = label
            parent_entry["color"] = badge_color
    scheme = "wss" if request.is_secure() else "ws"
    host = request.get_host()
    ws_url = f"{scheme}://{host}/ocpp/<CHARGE_POINT_ID>/"
    context = {
        "chargers": chargers,
        "charger_groups": charger_groups,
        "show_demo_notice": is_watchtower,
        "demo_ws_url": ws_url,
        "ws_rate_limit": store.MAX_CONNECTIONS_PER_IP,
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("partial") == "table":
        html = render_to_string(
            "ocpp/includes/dashboard_table_rows.html", context, request=request
        )
        return JsonResponse({"html": html})
    return render(request, "ocpp/dashboard.html", context)


@login_required(login_url="pages:login")
@landing("Charge Point Simulator")
@live_update()
def cp_simulator(request):
    """Public landing page to control the OCPP charge point simulator."""
    host_header = request.get_host()
    default_host, host_port = split_domain_port(host_header)
    if not default_host:
        default_host = "127.0.0.1"
    default_ws_port = request.get_port() or host_port or "8000"
    default_cp_paths = ["CP1", "CP2"]
    default_serial_numbers = default_cp_paths
    default_connector_id = 1
    default_rfid = "FFFFFFFF"
    default_vins = ["WP0ZZZ00000000000", "WAUZZZ00000000000"]

    message = ""
    dashboard_link: str | None = None
    if request.method == "POST":
        cp_idx = int(request.POST.get("cp") or 1)
        action = request.POST.get("action")
        if action == "start":
            ws_port_value = request.POST.get("ws_port")
            if ws_port_value is None:
                ws_port = int(default_ws_port) if default_ws_port else None
            elif ws_port_value.strip():
                ws_port = int(ws_port_value)
            else:
                ws_port = None
            sim_params = dict(
                host=request.POST.get("host") or default_host,
                ws_port=ws_port,
                cp_path=request.POST.get("cp_path") or default_cp_paths[cp_idx - 1],
                serial_number=request.POST.get("serial_number")
                or default_serial_numbers[cp_idx - 1],
                connector_id=int(
                    request.POST.get("connector_id") or default_connector_id
                ),
                rfid=request.POST.get("rfid") or default_rfid,
                vin=request.POST.get("vin") or default_vins[cp_idx - 1],
                duration=int(request.POST.get("duration") or 600),
                interval=float(request.POST.get("interval") or 5),
                kw_min=float(request.POST.get("kw_min") or 30),
                kw_max=float(request.POST.get("kw_max") or 60),
                pre_charge_delay=float(request.POST.get("pre_charge_delay") or 0),
                repeat=request.POST.get("repeat") or False,
                daemon=True,
                username=request.POST.get("username") or None,
                password=request.POST.get("password") or None,
            )
            try:
                started, status, log_file = _start_simulator(sim_params, cp=cp_idx)
                if started:
                    message = f"CP{cp_idx} started: {status}. Logs: {log_file}"
                    try:
                        dashboard_link = reverse(
                            "charger-status", args=[sim_params["cp_path"]]
                        )
                    except NoReverseMatch:  # pragma: no cover - defensive
                        dashboard_link = None
                else:
                    message = f"CP{cp_idx} {status}. Logs: {log_file}"
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Failed to start CP{cp_idx}: {exc}"
        elif action == "stop":
            try:
                _stop_simulator(cp=cp_idx)
                message = f"CP{cp_idx} stop requested."
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Failed to stop CP{cp_idx}: {exc}"
        else:
            message = "Unknown action."

    states_dict = get_simulator_state()
    state_list = [states_dict[1], states_dict[2]]
    params_jsons = [
        json.dumps(state_list[0].get("params", {}), indent=2),
        json.dumps(state_list[1].get("params", {}), indent=2),
    ]
    state_jsons = [
        _simulator_status_json(1),
        _simulator_status_json(2),
    ]

    context = {
        "message": message,
        "dashboard_link": dashboard_link,
        "states": state_list,
        "default_host": default_host,
        "default_ws_port": default_ws_port,
        "default_cp_paths": default_cp_paths,
        "default_serial_numbers": default_serial_numbers,
        "default_connector_id": default_connector_id,
        "default_rfid": default_rfid,
        "default_vins": default_vins,
        "params_jsons": params_jsons,
        "state_jsons": state_jsons,
    }
    return render(request, "ocpp/cp_simulator.html", context)


def charger_page(request, cid, connector=None):
    """Public landing page for a charger displaying usage guidance or progress."""
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(
        request.user, charger, request=request
    )
    if access_response is not None:
        return access_response
    rfid_cache: dict[str, dict[str, str | None]] = {}
    overview = _connector_overview(
        charger, request.user, rfid_cache=rfid_cache
    )
    sessions = _live_sessions(charger)
    tx = None
    active_connector_count = 0
    if charger.connector_id is None:
        if sessions:
            total_kw = 0.0
            start_times = [
                tx_obj.start_time for _, tx_obj in sessions if tx_obj.start_time
            ]
            for _, tx_obj in sessions:
                if tx_obj.kw:
                    total_kw += tx_obj.kw
            tx = SimpleNamespace(
                kw=total_kw, start_time=min(start_times) if start_times else None
            )
            active_connector_count = len(sessions)
    else:
        tx = (
            sessions[0][1]
            if sessions
            else store.get_transaction(cid, charger.connector_id)
        )
        if tx:
            active_connector_count = 1
    state_source = tx if charger.connector_id is not None else (sessions if sessions else None)
    state, color = _charger_state(charger, state_source)
    language_cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    available_languages = [
        str(code).strip()
        for code, _ in settings.LANGUAGES
        if str(code).strip()
    ]
    supported_languages = set(available_languages)
    charger_language = (charger.language or "es").strip()
    if charger_language not in supported_languages:
        fallback = "es" if "es" in supported_languages else ""
        if not fallback and available_languages:
            fallback = available_languages[0]
        charger_language = fallback
    if (
        charger_language
        and (
            not language_cookie
            or language_cookie not in supported_languages
            or language_cookie != charger_language
        )
    ):
        translation.activate(charger_language)
    current_language = translation.get_language()
    request.LANGUAGE_CODE = current_language
    preferred_language = charger_language or current_language
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": item["url"],
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    connector_overview = [
        item for item in overview if item["charger"].connector_id is not None
    ]
    status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    tx_rfid_details = _transaction_rfid_details(tx, cache=rfid_cache)
    return render(
        request,
        "ocpp/charger_page.html",
        {
            "charger": charger,
            "tx": tx,
            "tx_rfid_details": tx_rfid_details,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "connector_overview": connector_overview,
            "active_connector_count": active_connector_count,
            "status_url": status_url,
            "landing_translations": _landing_page_translations(),
            "preferred_language": preferred_language,
            "state": state,
            "color": color,
            "charger_error_code": _visible_error_code(charger.last_error_code),
        },
    )


@login_required
def charger_status(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(
        request.user, charger, request=request
    )
    if access_response is not None:
        return access_response
    session_id = request.GET.get("session")
    sessions = _live_sessions(charger)
    live_tx = None
    if charger.connector_id is not None and sessions:
        live_tx = sessions[0][1]
    tx_obj = live_tx
    past_session = False
    if session_id:
        if charger.connector_id is None:
            tx_obj = get_object_or_404(
                Transaction, pk=session_id, charger__charger_id=cid
            )
            past_session = True
        elif not (live_tx and str(live_tx.pk) == session_id):
            tx_obj = get_object_or_404(Transaction, pk=session_id, charger=charger)
            past_session = True
    state, color = _charger_state(
        charger,
        (
            live_tx
            if charger.connector_id is not None
            else (sessions if sessions else None)
        ),
    )
    if charger.connector_id is None:
        transactions_qs = (
            Transaction.objects.filter(charger__charger_id=cid)
            .select_related("charger")
            .order_by("-start_time")
        )
    else:
        transactions_qs = Transaction.objects.filter(charger=charger).order_by(
            "-start_time"
        )
    paginator = Paginator(transactions_qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))
    transactions = page_obj.object_list
    date_view = request.GET.get("dates", "charger").lower()
    if date_view not in {"charger", "received"}:
        date_view = "charger"

    def _date_query(mode: str) -> str:
        params = request.GET.copy()
        params["dates"] = mode
        query = params.urlencode()
        return f"?{query}" if query else ""

    date_view_options = {
        "charger": _("Charger timestamps"),
        "received": _("Received timestamps"),
    }
    date_toggle_links = [
        {
            "mode": mode,
            "label": label,
            "url": _date_query(mode),
            "active": mode == date_view,
        }
        for mode, label in date_view_options.items()
    ]
    chart_data = {"labels": [], "datasets": []}
    pagination_params = request.GET.copy()
    pagination_params["dates"] = date_view
    pagination_params.pop("page", None)
    pagination_query = pagination_params.urlencode()
    session_params = request.GET.copy()
    session_params["dates"] = date_view
    session_params.pop("session", None)
    session_params.pop("page", None)
    session_query = session_params.urlencode()

    def _series_from_transaction(tx):
        points: list[tuple[str, float]] = []
        readings = list(
            tx.meter_values.filter(energy__isnull=False).order_by("timestamp")
        )
        start_val = None
        if tx.meter_start is not None:
            start_val = float(tx.meter_start) / 1000.0
        for reading in readings:
            try:
                val = float(reading.energy)
            except (TypeError, ValueError):
                continue
            if start_val is None:
                start_val = val
            total = val - start_val
            points.append((reading.timestamp.isoformat(), max(total, 0.0)))
        return points

    if tx_obj and (charger.connector_id is not None or past_session):
        series_points = _series_from_transaction(tx_obj)
        if series_points:
            chart_data["labels"] = [ts for ts, _ in series_points]
            connector_id = None
            if tx_obj.charger and tx_obj.charger.connector_id is not None:
                connector_id = tx_obj.charger.connector_id
            elif charger.connector_id is not None:
                connector_id = charger.connector_id
            chart_data["datasets"].append(
                {
                    "label": str(
                        tx_obj.charger.connector_label
                        if tx_obj.charger and tx_obj.charger.connector_id is not None
                        else charger.connector_label
                    ),
                    "values": [value for _, value in series_points],
                    "connector_id": connector_id,
                }
            )
    elif charger.connector_id is None:
        dataset_points: list[tuple[str, list[tuple[str, float]], int]] = []
        for sibling, sibling_tx in sessions:
            if sibling.connector_id is None or not sibling_tx:
                continue
            points = _series_from_transaction(sibling_tx)
            if not points:
                continue
            dataset_points.append(
                (str(sibling.connector_label), points, sibling.connector_id)
            )
        if dataset_points:
            all_labels: list[str] = sorted(
                {ts for _, points, _ in dataset_points for ts, _ in points}
            )
            chart_data["labels"] = all_labels
            for label, points, connector_id in dataset_points:
                value_map = {ts: val for ts, val in points}
                chart_data["datasets"].append(
                    {
                        "label": label,
                        "values": [value_map.get(ts) for ts in all_labels],
                        "connector_id": connector_id,
                    }
                )
    rfid_cache: dict[str, dict[str, str | None]] = {}
    overview = _connector_overview(
        charger, request.user, rfid_cache=rfid_cache
    )
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": _reverse_connector_url("charger-status", cid, item["slug"]),
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    connector_overview = [
        item for item in overview if item["charger"].connector_id is not None
    ]
    usage_timeline, usage_timeline_window = _usage_timeline(
        charger, connector_overview
    )
    search_url = _reverse_connector_url("charger-session-search", cid, connector_slug)
    configuration_url = None
    if request.user.is_staff:
        try:
            configuration_url = reverse("admin:ocpp_charger_change", args=[charger.pk])
        except NoReverseMatch:  # pragma: no cover - admin may be disabled
            configuration_url = None
    is_connected = store.is_connected(cid, charger.connector_id)
    has_active_session = bool(
        live_tx if charger.connector_id is not None else sessions
    )
    can_remote_start = (
        charger.connector_id is not None
        and is_connected
        and not has_active_session
        and not past_session
    )
    remote_start_messages = None
    if can_remote_start:
        remote_start_messages = {
            "required": str(_("RFID is required to start a session.")),
            "sending": str(_("Sending remote start request...")),
            "success": str(_("Remote start command queued.")),
            "error": str(_("Unable to send remote start request.")),
        }
    action_url = _reverse_connector_url("charger-action", cid, connector_slug)
    chart_should_animate = bool(has_active_session and not past_session)

    tx_rfid_details = _transaction_rfid_details(tx_obj, cache=rfid_cache)

    return render(
        request,
        "ocpp/charger_status.html",
        {
            "charger": charger,
            "tx": tx_obj,
            "tx_rfid_details": tx_rfid_details,
            "state": state,
            "color": color,
            "transactions": transactions,
            "page_obj": page_obj,
            "chart_data": chart_data,
            "past_session": past_session,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
        "connector_overview": connector_overview,
        "search_url": search_url,
        "configuration_url": configuration_url,
        "page_url": _reverse_connector_url("charger-page", cid, connector_slug),
        "is_connected": is_connected,
        "is_idle": is_connected and not has_active_session,
        "can_remote_start": can_remote_start,
        "remote_start_messages": remote_start_messages,
        "action_url": action_url,
        "show_chart": bool(
            chart_data["datasets"]
            and any(
                any(value is not None for value in dataset["values"])
                for dataset in chart_data["datasets"]
            )
        ),
        "date_view": date_view,
        "date_toggle_links": date_toggle_links,
        "pagination_query": pagination_query,
        "session_query": session_query,
        "chart_should_animate": chart_should_animate,
        "usage_timeline": usage_timeline,
        "usage_timeline_window": usage_timeline_window,
        "charger_error_code": _visible_error_code(charger.last_error_code),
    },
)


@login_required
def charger_session_search(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(
        request.user, charger, request=request
    )
    if access_response is not None:
        return access_response
    date_str = request.GET.get("date")
    date_view = request.GET.get("dates", "charger").lower()
    if date_view not in {"charger", "received"}:
        date_view = "charger"

    def _date_query(mode: str) -> str:
        params = request.GET.copy()
        params["dates"] = mode
        query = params.urlencode()
        return f"?{query}" if query else ""

    date_toggle_links = [
        {
            "mode": mode,
            "label": label,
            "url": _date_query(mode),
            "active": mode == date_view,
        }
        for mode, label in {
            "charger": _("Charger timestamps"),
            "received": _("Received timestamps"),
        }.items()
    ]
    transactions = None
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            start = datetime.combine(
                date_obj, datetime.min.time(), tzinfo=dt_timezone.utc
            )
            end = start + timedelta(days=1)
            qs = Transaction.objects.filter(start_time__gte=start, start_time__lt=end)
            if charger.connector_id is None:
                qs = qs.filter(charger__charger_id=cid)
            else:
                qs = qs.filter(charger=charger)
            transactions = qs.order_by("-start_time")
        except ValueError:
            transactions = []
    if transactions is not None:
        transactions = list(transactions)
        rfid_cache: dict[str, dict[str, str | None]] = {}
        for tx in transactions:
            details = _transaction_rfid_details(tx, cache=rfid_cache)
            label_value = None
            if details:
                label_value = str(details.get("label") or "").strip() or None
            tx.rfid_label = label_value
    overview = _connector_overview(charger, request.user)
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": _reverse_connector_url("charger-session-search", cid, item["slug"]),
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    return render(
        request,
        "ocpp/charger_session_search.html",
        {
            "charger": charger,
            "transactions": transactions,
            "date": date_str,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "status_url": status_url,
            "date_view": date_view,
            "date_toggle_links": date_toggle_links,
        },
    )


@login_required
def charger_log_page(request, cid, connector=None):
    """Render a simple page with the log for the charger or simulator."""
    log_type = request.GET.get("type", "charger")
    connector_links = []
    connector_slug = None
    status_url = None
    if log_type == "charger":
        charger, connector_slug = _get_charger(cid, connector)
        access_response = _ensure_charger_access(
            request.user, charger, request=request
        )
        if access_response is not None:
            return access_response
        log_key = store.identity_key(cid, charger.connector_id)
        overview = _connector_overview(charger, request.user)
        connector_links = [
            {
                "slug": item["slug"],
                "label": item["label"],
                "url": _reverse_connector_url("charger-log", cid, item["slug"]),
                "active": item["slug"] == connector_slug,
            }
            for item in overview
        ]
        target_id = log_key
        status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    else:
        charger = Charger.objects.filter(charger_id=cid).first() or Charger(
            charger_id=cid
        )
        target_id = cid

    slug_source = slugify(target_id) or slugify(cid) or "log"
    filename_parts = [log_type, slug_source]
    download_filename = f"{'-'.join(part for part in filename_parts if part)}.log"
    limit_options = [
        {"value": "20", "label": "20"},
        {"value": "40", "label": "40"},
        {"value": "100", "label": "100"},
        {"value": "all", "label": gettext("All")},
    ]
    allowed_values = [item["value"] for item in limit_options]
    limit_choice = request.GET.get("limit", "20")
    if limit_choice not in allowed_values:
        limit_choice = "20"
    limit_index = allowed_values.index(limit_choice)

    download_requested = request.GET.get("download") == "1"

    limit_value: int | None = None
    if limit_choice != "all":
        try:
            limit_value = int(limit_choice)
        except (TypeError, ValueError):
            limit_value = 20
            limit_choice = "20"
            limit_index = allowed_values.index(limit_choice)
    log_entries: list[str]
    if download_requested:
        log_entries = list(store.get_logs(target_id, log_type=log_type) or [])
        download_content = "\n".join(log_entries)
        if download_content and not download_content.endswith("\n"):
            download_content = f"{download_content}\n"
        response = HttpResponse(download_content, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{download_filename}"'
        return response

    log_entries = list(
        store.get_logs(target_id, log_type=log_type, limit=limit_value) or []
    )

    download_params = request.GET.copy()
    download_params["download"] = "1"
    download_params.pop("limit", None)
    download_query = download_params.urlencode()
    log_download_url = f"{request.path}?{download_query}" if download_query else request.path

    limit_label = limit_options[limit_index]["label"]
    log_content = "\n".join(log_entries)
    return render(
        request,
        "ocpp/charger_logs.html",
        {
            "charger": charger,
            "log": log_entries,
            "log_content": log_content,
            "log_type": log_type,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "status_url": status_url,
            "log_limit_options": limit_options,
            "log_limit_index": limit_index,
            "log_limit_choice": limit_choice,
            "log_limit_label": limit_label,
            "log_download_url": log_download_url,
            "log_filename": download_filename,
        },
    )


@csrf_exempt
@api_login_required
def dispatch_action(request, cid, connector=None):
    connector_value, _normalized_slug = _normalize_connector_slug(connector)
    log_key = store.identity_key(cid, connector_value)
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

    access_response = _ensure_charger_access(
        request.user, charger_obj, request=request
    )
    if access_response is not None:
        return access_response
    ws = store.get_connection(cid, connector_value)
    if ws is None:
        return JsonResponse({"detail": "no connection"}, status=404)
    try:
        data = json.loads(request.body.decode()) if request.body else {}
    except json.JSONDecodeError:
        data = {}
    action = data.get("action")
    message_id: str | None = None
    ocpp_action: str | None = None
    expected_statuses: set[str] | None = None
    msg: str | None = None
    if action == "remote_stop":
        tx_obj = store.get_transaction(cid, connector_value)
        if not tx_obj:
            return JsonResponse({"detail": "no transaction"}, status=404)
        message_id = uuid.uuid4().hex
        ocpp_action = "RemoteStopTransaction"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        msg = json.dumps(
            [
                2,
                message_id,
                "RemoteStopTransaction",
                {"transactionId": tx_obj.pk},
            ]
        )
        async_to_sync(ws.send)(msg)
        store.register_pending_call(
            message_id,
            {
                "action": "RemoteStopTransaction",
                "charger_id": cid,
                "connector_id": connector_value,
                "log_key": log_key,
                "transaction_id": tx_obj.pk,
                "requested_at": timezone.now(),
            },
        )
    elif action == "remote_start":
        id_tag = data.get("idTag")
        if not isinstance(id_tag, str) or not id_tag.strip():
            return JsonResponse({"detail": "idTag required"}, status=400)
        id_tag = id_tag.strip()
        payload: dict[str, object] = {"idTag": id_tag}
        connector_id = data.get("connectorId")
        if connector_id in ("", None):
            connector_id = None
        if connector_id is None and connector_value is not None:
            connector_id = connector_value
        if connector_id is not None:
            try:
                payload["connectorId"] = int(connector_id)
            except (TypeError, ValueError):
                payload["connectorId"] = connector_id
        if "chargingProfile" in data and data["chargingProfile"] is not None:
            payload["chargingProfile"] = data["chargingProfile"]
        message_id = uuid.uuid4().hex
        ocpp_action = "RemoteStartTransaction"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        msg = json.dumps(
            [
                2,
                message_id,
                "RemoteStartTransaction",
                payload,
            ]
        )
        async_to_sync(ws.send)(msg)
        store.register_pending_call(
            message_id,
            {
                "action": "RemoteStartTransaction",
                "charger_id": cid,
                "connector_id": connector_value,
                "log_key": log_key,
                "id_tag": id_tag,
                "requested_at": timezone.now(),
            },
        )
    elif action == "change_availability":
        availability_type = data.get("type")
        if availability_type not in {"Operative", "Inoperative"}:
            return JsonResponse({"detail": "invalid availability type"}, status=400)
        connector_payload = connector_value if connector_value is not None else 0
        if "connectorId" in data:
            candidate = data.get("connectorId")
            if candidate not in (None, ""):
                try:
                    connector_payload = int(candidate)
                except (TypeError, ValueError):
                    connector_payload = candidate
        message_id = uuid.uuid4().hex
        ocpp_action = "ChangeAvailability"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        payload = {"connectorId": connector_payload, "type": availability_type}
        msg = json.dumps([2, message_id, "ChangeAvailability", payload])
        async_to_sync(ws.send)(msg)
        requested_at = timezone.now()
        store.register_pending_call(
            message_id,
            {
                "action": "ChangeAvailability",
                "charger_id": cid,
                "connector_id": connector_value,
                "availability_type": availability_type,
                "requested_at": requested_at,
            },
        )
        if charger_obj:
            updates = {
                "availability_requested_state": availability_type,
                "availability_requested_at": requested_at,
                "availability_request_status": "",
                "availability_request_status_at": None,
                "availability_request_details": "",
            }
            Charger.objects.filter(pk=charger_obj.pk).update(**updates)
            for field, value in updates.items():
                setattr(charger_obj, field, value)
    elif action == "change_configuration":
        raw_key = data.get("key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            return JsonResponse({"detail": "key required"}, status=400)
        key_value = raw_key.strip()
        raw_value = data.get("value", None)
        value_included = False
        value_text: str | None = None
        if raw_value is not None:
            if isinstance(raw_value, (str, int, float, bool)):
                value_included = True
                if isinstance(raw_value, str):
                    value_text = raw_value
                else:
                    value_text = str(raw_value)
            else:
                return JsonResponse(
                    {"detail": "value must be a string, number, or boolean"},
                    status=400,
                )
        payload = {"key": key_value}
        if value_included:
            payload["value"] = value_text
        message_id = uuid.uuid4().hex
        ocpp_action = "ChangeConfiguration"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        msg = json.dumps([2, message_id, "ChangeConfiguration", payload])
    elif action == "clear_cache":
        message_id = uuid.uuid4().hex
        ocpp_action = "ClearCache"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        msg = json.dumps([2, message_id, "ClearCache", {}])
        async_to_sync(ws.send)(msg)
        requested_at = timezone.now()
        store.register_pending_call(
            message_id,
            {
                "action": "ChangeConfiguration",
                "charger_id": cid,
                "connector_id": connector_value,
                "log_key": log_key,
                "key": key_value,
                "value": value_text,
                "requested_at": requested_at,
            },
        )
        timeout_message = str(_("Change configuration request timed out."))
        store.schedule_call_timeout(
            message_id,
            action="ChangeConfiguration",
            log_key=log_key,
            message=timeout_message,
        )
        if value_included and value_text is not None:
            change_message = str(
                _("Requested configuration change for %(key)s to %(value)s")
                % {"key": key_value, "value": value_text}
            )
        else:
            change_message = str(
                _("Requested configuration change for %(key)s")
                % {"key": key_value}
            )
        store.add_log(log_key, change_message, log_type="charger")
    elif action == "data_transfer":
        vendor_id = data.get("vendorId")
        if not isinstance(vendor_id, str) or not vendor_id.strip():
            return JsonResponse({"detail": "vendorId required"}, status=400)
        vendor_id = vendor_id.strip()
        payload: dict[str, object] = {"vendorId": vendor_id}
        message_identifier = ""
        if "messageId" in data and data["messageId"] is not None:
            message_candidate = data["messageId"]
            if not isinstance(message_candidate, str):
                return JsonResponse({"detail": "messageId must be a string"}, status=400)
            message_identifier = message_candidate.strip()
            if message_identifier:
                payload["messageId"] = message_identifier
        if "data" in data:
            payload["data"] = data["data"]
        message_id = uuid.uuid4().hex
        ocpp_action = "DataTransfer"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        msg = json.dumps([2, message_id, "DataTransfer", payload])
        record = DataTransferMessage.objects.create(
            charger=charger_obj,
            connector_id=connector_value,
            direction=DataTransferMessage.DIRECTION_CSMS_TO_CP,
            ocpp_message_id=message_id,
            vendor_id=vendor_id,
            message_id=message_identifier,
            payload=payload,
            status="Pending",
        )
        async_to_sync(ws.send)(msg)
        store.register_pending_call(
            message_id,
            {
                "action": "DataTransfer",
                "charger_id": cid,
                "connector_id": connector_value,
                "message_pk": record.pk,
                "log_key": log_key,
            },
        )
    elif action == "reset":
        tx_obj = store.get_transaction(cid, connector_value)
        if tx_obj is not None:
            detail = _(
                "Reset is blocked while a charging session is active. "
                "Stop the session first."
            )
            return JsonResponse({"detail": detail}, status=409)
        message_id = uuid.uuid4().hex
        ocpp_action = "Reset"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        msg = json.dumps([2, message_id, "Reset", {"type": "Soft"}])
        async_to_sync(ws.send)(msg)
        store.register_pending_call(
            message_id,
            {
                "action": "Reset",
                "charger_id": cid,
                "connector_id": connector_value,
                "log_key": log_key,
                "requested_at": timezone.now(),
            },
        )
    elif action == "trigger_message":
        trigger_target = data.get("target") or data.get("triggerTarget")
        if not isinstance(trigger_target, str) or not trigger_target.strip():
            return JsonResponse({"detail": "target required"}, status=400)
        trigger_target = trigger_target.strip()
        allowed_targets = {
            "BootNotification",
            "DiagnosticsStatusNotification",
            "FirmwareStatusNotification",
            "Heartbeat",
            "MeterValues",
            "StatusNotification",
        }
        if trigger_target not in allowed_targets:
            return JsonResponse({"detail": "invalid target"}, status=400)
        payload: dict[str, object] = {"requestedMessage": trigger_target}
        trigger_connector = None
        connector_field = data.get("connectorId")
        if connector_field in (None, ""):
            connector_field = data.get("connector")
        if connector_field in (None, "") and connector_value is not None:
            connector_field = connector_value
        if connector_field not in (None, ""):
            try:
                trigger_connector = int(connector_field)
            except (TypeError, ValueError):
                return JsonResponse({"detail": "connectorId must be an integer"}, status=400)
            if trigger_connector <= 0:
                return JsonResponse({"detail": "connectorId must be positive"}, status=400)
            payload["connectorId"] = trigger_connector
        message_id = uuid.uuid4().hex
        ocpp_action = "TriggerMessage"
        expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
        msg = json.dumps([2, message_id, "TriggerMessage", payload])
        async_to_sync(ws.send)(msg)
        store.register_pending_call(
            message_id,
            {
                "action": "TriggerMessage",
                "charger_id": cid,
                "connector_id": connector_value,
                "log_key": log_key,
                "trigger_target": trigger_target,
                "trigger_connector": trigger_connector,
                "requested_at": timezone.now(),
            },
        )
    else:
        return JsonResponse({"detail": "unknown action"}, status=400)
    log_key = store.identity_key(cid, connector_value)
    if msg is None or message_id is None or ocpp_action is None:
        return JsonResponse({"detail": "unknown action"}, status=400)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    expected_statuses = expected_statuses or CALL_EXPECTED_STATUSES.get(ocpp_action)
    success, detail, status_code = _evaluate_pending_call_result(
        message_id,
        ocpp_action,
        expected_statuses=expected_statuses,
    )
    if not success:
        return JsonResponse({"detail": detail}, status=status_code or 400)
    return JsonResponse({"sent": msg})
