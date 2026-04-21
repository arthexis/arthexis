import logging
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.docs import rendering
from apps.features.utils import get_cached_feature_enabled, get_cached_feature_parameter
from apps.ocpp.models.location import Location
from apps.ocpp.services import ChargerAccessDeniedError, build_charger_chart_payload
from apps.sites.utils import (
    landing,
    module_pill_link_validation,
    require_site_operator_or_staff,
)

from ..models import annotate_transaction_energy_bounds, StationModel, Transaction
from .common import *  # noqa: F401,F403
from .common import (
    _charger_state,
    _charging_limit_details,
    _clear_stale_statuses_for_view,
    _connector_overview,
    _connector_set,
    _ensure_charger_access,
    _get_charger,
    _important_non_transaction_events,
    _landing_page_translations,
    _landing_requires_chargers,
    _landing_visibility_params,
    _live_sessions,
    _reverse_connector_url,
    _status_badge_class,
    _transaction_rfid_details,
    _usage_timeline,
    _visible_chargers,
    _visible_error_code,
)

logger = logging.getLogger(__name__)
ENERGY_ACCOUNTS_FEATURE_SLUG = "energy-accounts"


def _energy_accounts_enabled() -> bool:
    return get_cached_feature_enabled(
        ENERGY_ACCOUNTS_FEATURE_SLUG,
        cache_key="feature-enabled:energy-accounts",
        timeout=300,
        default=False,
    )


@landing("Charging Station Map")
@module_pill_link_validation(
    _landing_requires_chargers,
    parameter_getter=_landing_visibility_params,
)
def charging_station_map(request):
    """Render the charging map for authorized operator/staff users.

    Parameters:
        request: Incoming HTTP request containing user/session context.

    Returns:
        HttpResponse: Rendered charging map page or an auth redirect response.

    Raises:
        Http404: Propagated if downstream data access raises it.
    """
    auth_response = require_site_operator_or_staff(request)
    if auth_response is not None:
        return auth_response
    location_ids = (
        _visible_chargers(request.user)
        .select_related("location")
        .filter(
            location__isnull=False,
            location__latitude__isnull=False,
            location__longitude__isnull=False,
        )
        .values_list("location_id", flat=True)
        .distinct()
    )
    locations_qs = Location.objects.filter(pk__in=location_ids).order_by("name")
    locations = [
        {
            "id": location.pk,
            "name": location.name,
            "latitude": float(location.latitude),
            "longitude": float(location.longitude),
            "directions_url": (
                "https://www.google.com/maps/dir/?api=1&destination="
                f"{float(location.latitude)},{float(location.longitude)}"
            ),
        }
        for location in locations_qs
    ]
    return render(
        request,
        "ocpp/charging_station_map.html",
        {
            "locations": locations,
            "initial_location": locations[0] if locations else None,
        },
    )


def charger_page(request, cid, connector=None):
    """Public landing page for a charger displaying usage guidance or progress."""
    _clear_stale_statuses_for_view()
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(request.user, charger, request=request)
    if access_response is not None:
        return access_response

    if connector is None and charger.connector_id is None:
        default_connector = next(
            (
                sibling
                for sibling in _connector_set(charger)
                if sibling.connector_id is not None
                and sibling.is_visible_to(request.user)
            ),
            None,
        )
        if default_connector is not None:
            return redirect(
                "ocpp:charger-page-connector",
                cid,
                default_connector.connector_slug,
            )

    connectors = _connector_set(charger)
    rfid_cache: dict[str, dict[str, str | None]] = {}
    overview = _connector_overview(
        charger,
        request.user,
        connectors=connectors,
        rfid_cache=rfid_cache,
    )
    sessions = _live_sessions(charger, connectors=connectors)
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
    state_source = (
        tx if charger.connector_id is not None else (sessions if sessions else None)
    )
    state, color = _charger_state(charger, state_source)
    preferred_language = "en"
    connector_links = [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": item["url"],
            "active": item["slug"] == connector_slug,
        }
        for item in overview
    ]
    connector_switch_links = [
        item
        for item in connector_links
        if item["slug"] != Charger.AGGREGATE_CONNECTOR_SLUG
    ]
    connector_prev_url = ""
    connector_next_url = ""
    if connector_switch_links:
        active_index = next(
            (idx for idx, item in enumerate(connector_switch_links) if item["active"]),
            0,
        )
        if len(connector_switch_links) > 1:
            connector_prev_url = connector_switch_links[
                (active_index - 1) % len(connector_switch_links)
            ]["url"]
            connector_next_url = connector_switch_links[
                (active_index + 1) % len(connector_switch_links)
            ]["url"]
    connector_overview = [
        item for item in overview if item["charger"].connector_id is not None
    ]
    status_url = _reverse_connector_url("charger-status", cid, connector_slug)
    account_summary_url = (
        _reverse_connector_url("charger-account-summary", cid, connector_slug)
        if request.user.is_authenticated
        else ""
    )
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
            "connector_switch_links": connector_switch_links,
            "connector_prev_url": connector_prev_url,
            "connector_next_url": connector_next_url,
            "connector_overview": connector_overview,
            "active_connector_count": active_connector_count,
            "account_summary_url": account_summary_url,
            "status_url": status_url,
            "energy_accounts_enabled": _energy_accounts_enabled(),
            "landing_translations": _landing_page_translations(),
            "preferred_language": preferred_language,
            "state": state,
            "color": color,
            "status_badge_class": _status_badge_class(state, color),
            "charger_error_code": _visible_error_code(charger.last_error_code),
        },
    )


@login_required
def charger_status(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(request.user, charger, request=request)
    if access_response is not None:
        return access_response
    connectors = [
        item for item in _connector_set(charger) if item.is_visible_to(request.user)
    ]
    connector_count = len(
        [item for item in connectors if item.connector_id is not None]
    )
    show_combined_view = connector_count > 1
    if show_combined_view and charger.connector_id is not None:
        aggregate = next(
            (item for item in connectors if item.connector_id is None), None
        )
        if aggregate is not None:
            # Ensure the user has access to the aggregate view before swapping.
            # If access is denied, fall back to the single connector view.
            if _ensure_charger_access(request.user, aggregate, request=request) is None:
                charger = aggregate
                connector_slug = Charger.AGGREGATE_CONNECTOR_SLUG
        else:
            logger.warning(
                "Aggregate charger missing for %s while rendering status view.",
                charger.charger_id,
            )
    session_id = request.GET.get("session")
    sessions = _live_sessions(charger, connectors=connectors)
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
    transactions = list(transactions_qs[:5])
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
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    pagination_query = pagination_params.urlencode()
    session_params = request.GET.copy()
    session_params.pop("session", None)
    session_query = session_params.urlencode()
    chart_data = build_charger_chart_payload(
        user=request.user,
        cid=cid,
        connector=connector_slug,
        session_id=session_id,
    )
    rfid_cache: dict[str, dict[str, str | None]] = {}
    overview = _connector_overview(
        charger,
        request.user,
        connectors=connectors,
        rfid_cache=rfid_cache,
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
    can_view_sensitive_non_transaction_events = request.user.is_staff or (
        charger.has_owner_scope() and charger.is_visible_to(request.user)
    )
    non_transaction_events = _important_non_transaction_events(
        charger,
        charger,
        include_sensitive=can_view_sensitive_non_transaction_events,
    )
    show_connector_tabs = False
    show_connector_overview_cards = charger.connector_id is None and connector_count > 1
    usage_timeline, usage_timeline_window = _usage_timeline(charger, connector_overview)
    search_url = _reverse_connector_url("charger-session-search", cid, connector_slug)
    configuration_url = None
    transactions_admin_url = None
    if request.user.is_staff:
        try:
            configuration_url = reverse("admin:ocpp_charger_change", args=[charger.pk])
        except NoReverseMatch:  # pragma: no cover - admin may be disabled
            configuration_url = None
        try:
            transactions_admin_url = reverse("admin:ocpp_transaction_changelist")
        except NoReverseMatch:  # pragma: no cover - admin may be disabled
            transactions_admin_url = None
    is_connected = store.is_connected(cid, charger.connector_id)
    has_active_session = bool(live_tx if charger.connector_id is not None else sessions)
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
    is_live_session_view = bool(has_active_session and not past_session)
    chart_should_animate = is_live_session_view
    status_should_poll = is_live_session_view

    tx_rfid_details = _transaction_rfid_details(tx_obj, cache=rfid_cache)

    chart_data_url = _reverse_connector_url("charger-status-chart", cid, connector_slug)

    return render(
        request,
        "ocpp/charger_status.html",
        {
            "charger": charger,
            "tx": tx_obj,
            "tx_rfid_details": tx_rfid_details,
            "state": state,
            "color": color,
            "status_badge_class": _status_badge_class(state, color),
            "transactions": transactions,
            "non_transaction_events": non_transaction_events,
            "page_obj": None,
            "chart_data": chart_data,
            "past_session": past_session,
            "connector_slug": connector_slug,
            "connector_links": connector_links,
            "connector_overview": connector_overview,
            "search_url": search_url,
            "configuration_url": configuration_url,
            "transactions_admin_url": transactions_admin_url,
            "can_view_transaction_links": bool(
                request.user.is_staff and transactions_admin_url
            ),
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
            "status_should_poll": status_should_poll,
            "chart_data_url": chart_data_url,
            "chart_session": session_id,
            "usage_timeline": usage_timeline,
            "usage_timeline_window": usage_timeline_window,
            "charger_error_code": _visible_error_code(charger.last_error_code),
            "show_connector_tabs": show_connector_tabs,
            "show_connector_overview_cards": show_connector_overview_cards,
            "charging_limit": _charging_limit_details(charger),
            "hide_default_footer": True,
        },
    )


@login_required
@require_GET
def charger_status_chart(request, cid, connector=None):
    """Return charger status chart data for authenticated users as JSON.

    Parameters:
        request: Incoming authenticated HTTP request.
        cid: Charger identifier.
        connector: Optional connector slug from the URL.

    Returns:
        JsonResponse: Chart payload matching the charger status template contract.

    Raises:
        Http404: Propagated when the charger cannot be found.
    """

    not_found_detail = {"detail": _("Not found.")}
    session_id = request.GET.get("session") or None
    try:
        payload = build_charger_chart_payload(
            user=request.user,
            cid=cid,
            connector=connector,
            session_id=session_id,
        )
    except ChargerAccessDeniedError as exc:
        logger.warning("Denied charger status chart access for cid=%s: %s", cid, exc)
        return JsonResponse(not_found_detail, status=404)
    except Transaction.DoesNotExist as exc:
        logger.info(
            "Missing transaction for charger status chart cid=%s session=%s: %s",
            cid,
            session_id,
            exc,
        )
        return JsonResponse(not_found_detail, status=404)
    return JsonResponse(payload)


@login_required
def charger_session_search(request, cid, connector=None):
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(request.user, charger, request=request)
    if access_response is not None:
        return access_response
    connectors = _connector_set(charger)
    date_str = request.GET.get("date")
    quick_range = request.GET.get("range", "").lower()
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
    filtered_count = 0
    filtered_total_kw = 0.0
    if date_str or quick_range:
        try:
            if quick_range == "today":
                start_date = timezone.localdate()
                end_date = start_date + timedelta(days=1)
            elif quick_range == "yesterday":
                end_date = timezone.localdate()
                start_date = end_date - timedelta(days=1)
            elif quick_range == "last7":
                end_date = timezone.localdate() + timedelta(days=1)
                start_date = end_date - timedelta(days=7)
            else:
                if not date_str:
                    raise ValueError("Missing date")
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                start_date = date_obj
                end_date = start_date + timedelta(days=1)
                quick_range = ""
            start = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
            end = timezone.make_aware(datetime.combine(end_date, datetime.min.time()))
            qs = Transaction.objects.filter(start_time__gte=start, start_time__lt=end)
            if charger.connector_id is None:
                qs = qs.filter(charger__charger_id=cid)
            else:
                qs = qs.filter(charger=charger)
            transactions = annotate_transaction_energy_bounds(qs).order_by("-start_time")
        except ValueError:
            transactions = []
            quick_range = ""
    if transactions is not None:
        transactions = list(transactions)
        rfid_cache: dict[str, dict[str, str | None]] = {}
        for tx in transactions:
            details = _transaction_rfid_details(tx, cache=rfid_cache)
            label_value = None
            if details:
                label_value = str(details.get("label") or "").strip() or None
            tx.rfid_label = label_value
        filtered_count = len(transactions)
        filtered_total_kw = sum(tx.kw or 0 for tx in transactions)
    overview = _connector_overview(charger, request.user, connectors=connectors)
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
            "quick_range": quick_range,
            "filtered_count": filtered_count,
            "filtered_total_kw": filtered_total_kw,
            "hide_default_footer": True,
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
        access_response = _ensure_charger_access(request.user, charger, request=request)
        if access_response is not None:
            return access_response
        connectors = _connector_set(charger)
        log_key = store.identity_key(cid, charger.connector_id)
        overview = _connector_overview(charger, request.user, connectors=connectors)
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
        response = HttpResponse(
            download_content, content_type="text/plain; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{download_filename}"'
        return response

    log_entries = list(
        store.get_logs(target_id, log_type=log_type, limit=limit_value) or []
    )

    download_params = request.GET.copy()
    download_params["download"] = "1"
    download_params.pop("limit", None)
    download_query = download_params.urlencode()
    log_download_url = (
        f"{request.path}?{download_query}" if download_query else request.path
    )

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
            "hide_default_footer": True,
        },
    )


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _station_model_images(bucket):
    if not bucket:
        return []
    files = bucket.files.all().order_by("-uploaded_at")
    images = []
    for media_file in files:
        content_type = (media_file.content_type or "").lower()
        name = media_file.original_name or ""
        extension = Path(name).suffix.lower()
        if content_type.startswith("image/") or extension in IMAGE_EXTENSIONS:
            images.append(media_file)
    return images


def _station_model_documents(bucket, image_ids):
    if not bucket:
        return []
    files = bucket.files.all().order_by("-uploaded_at")
    return [media_file for media_file in files if media_file.pk not in image_ids]


def _landing_requires_station_models(*, request, landing, **kwargs) -> bool:
    return StationModel.objects.exists()


@landing("Supported CP Models")
@module_pill_link_validation(
    _landing_requires_station_models,
    parameter_getter=_landing_visibility_params,
)
def supported_chargers(request):
    station_models = StationModel.objects.all().order_by(
        "vendor", "model_family", "model"
    )
    return render(
        request,
        "ocpp/supported_chargers.html",
        {"station_models": station_models},
    )


def supported_charger_detail(request, station_model_id: int):
    station_model = get_object_or_404(StationModel, pk=station_model_id)
    instructions_html, _ = rendering.render_markdown_with_toc(
        station_model.instructions_markdown or ""
    )
    images_bucket = station_model.images_bucket
    documents_bucket = station_model.documents_bucket
    if images_bucket and images_bucket == documents_bucket:
        files = images_bucket.files.all().order_by("-uploaded_at")
        images = []
        documents = []
        for media_file in files:
            content_type = (media_file.content_type or "").lower()
            name = media_file.original_name or ""
            extension = Path(name).suffix.lower()
            if content_type.startswith("image/") or extension in IMAGE_EXTENSIONS:
                images.append(media_file)
            else:
                documents.append(media_file)
    else:
        images = _station_model_images(images_bucket)
        image_ids = {image.pk for image in images}
        documents = _station_model_documents(documents_bucket, image_ids)
    return render(
        request,
        "ocpp/supported_charger_detail.html",
        {
            "station_model": station_model,
            "instructions_html": instructions_html,
            "images": images,
            "documents": documents,
        },
    )
