from django.utils.translation import gettext as _

from .common import *  # noqa: F401,F403
from .common import (
    _charger_state,
    _clear_stale_statuses_for_view,
    _connector_overview,
    _connector_set,
    _default_language_code,
    _ensure_charger_access,
    _get_charger,
    _landing_page_translations,
    _live_sessions,
    _charging_limit_details,
    _reverse_connector_url,
    _supported_language_codes,
    _transaction_rfid_details,
    _usage_timeline,
    _visible_error_code,
)

def charger_page(request, cid, connector=None):
    """Public landing page for a charger displaying usage guidance or progress."""
    _clear_stale_statuses_for_view()
    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(
        request.user, charger, request=request
    )
    if access_response is not None:
        return access_response
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
    state_source = tx if charger.connector_id is not None else (sessions if sessions else None)
    state, color = _charger_state(charger, state_source)
    language_cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    available_languages = _supported_language_codes()
    supported_languages = set(available_languages)
    language_candidates: list[str] = []
    connector_language = charger.language_code()
    if connector_language:
        language_candidates.append(connector_language)
    if charger.connector_id is not None:
        parent_language = (
            Charger.objects.filter(
                charger_id=charger.charger_id, connector_id=None
            )
            .values_list("language__code", flat=True)
            .first()
            or ""
        ).strip()
        if parent_language:
            language_candidates.append(parent_language)
    fallback_language = _default_language_code()
    if fallback_language and fallback_language in supported_languages:
        language_candidates.append(fallback_language)
    elif available_languages:
        language_candidates.append(available_languages[0])
    charger_language = ""
    for code in language_candidates:
        if code in supported_languages:
            charger_language = code
            break
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
    connectors = _connector_set(charger)
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
    connector_count = len(connector_overview)
    show_connector_tabs = connector_count > 1
    show_connector_overview_cards = (
        charger.connector_id is None and connector_count > 1
    )
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
        "show_connector_tabs": show_connector_tabs,
        "show_connector_overview_cards": show_connector_overview_cards,
        "charging_limit": _charging_limit_details(charger),
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
    connectors = _connector_set(charger)
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
    overview = _connector_overview(
        charger, request.user, connectors=connectors
    )
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
        connectors = _connector_set(charger)
        log_key = store.identity_key(cid, charger.connector_id)
        overview = _connector_overview(
            charger, request.user, connectors=connectors
        )
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

