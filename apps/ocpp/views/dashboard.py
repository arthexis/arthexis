from datetime import datetime, time, timedelta

from django.contrib.auth.views import redirect_to_login
from django.db.models import (
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _

from apps.nodes.models import Node
from apps.sites.utils import landing
from config.request_utils import is_https_request

from .. import store
from ..models import Charger, Transaction, annotate_transaction_energy_bounds
from ..status_display import STATUS_BADGE_MAP
from . import common as view_common
from .common import (
    _charger_last_seen,
    _charger_state,
    _charging_limit_details,
    _clear_stale_statuses_for_view,
    _has_active_session,
    _reverse_connector_url,
)


@landing("CPMS Online Dashboard")
def dashboard(request):
    """Landing page listing all known chargers and their status."""
    is_htmx = request.headers.get("HX-Request") == "true"
    _clear_stale_statuses_for_view()
    node = Node.get_local()
    role = node.role if node else None
    role_name = role.name if role else ""
    allow_anonymous_roles = {"Watchtower", "Constellation", "Satellite", "Terminal"}
    if not request.user.is_authenticated and role_name not in allow_anonymous_roles:
        return redirect_to_login(
            request.get_full_path(), login_url=reverse("pages:login")
        )
    is_watchtower = role_name in {"Watchtower", "Constellation"}
    latest_tx_subquery = (
        Transaction.objects.filter(charger=OuterRef("pk"))
        .order_by("-start_time")
        .values("pk")[:1]
    )
    visible_chargers_qs = (
        view_common._visible_chargers(request.user)
        .select_related("location")
        .annotate(latest_tx_id=Subquery(latest_tx_subquery))
        .order_by("charger_id", "connector_id")
    )
    visible_chargers = list(visible_chargers_qs)
    charger_ids = [charger.pk for charger in visible_chargers if charger.pk]
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

    def _tx_started_within(tx_obj, start, end) -> bool:
        start_time = getattr(tx_obj, "start_time", None)
        if start_time is None:
            return False
        if timezone.is_naive(start_time):
            start_time = timezone.make_aware(start_time, tz)
        return start <= start_time < end

    def _batch_charger_stats(charger_pks: list[int]) -> dict[int, dict[str, float]]:
        if not charger_pks:
            return {}

        annotated = annotate_transaction_energy_bounds(
            Transaction.objects.filter(charger_id__in=charger_pks)
        ).annotate(
            kw_value=Coalesce(
                ExpressionWrapper(
                    (F("meter_stop") - F("meter_start")) / Value(1000.0),
                    output_field=FloatField(),
                ),
                ExpressionWrapper(
                    F("meter_energy_end") - F("meter_energy_start"),
                    output_field=FloatField(),
                ),
                output_field=FloatField(),
            )
        )

        lifetime_totals = dict(
            annotated.values("charger_id")
            .annotate(total_kw=Sum("kw_value"))
            .values_list("charger_id", "total_kw")
        )
        today_totals = dict(
            annotated.filter(start_time__gte=day_start, start_time__lt=day_end)
            .values("charger_id")
            .annotate(total_kw=Sum("kw_value"))
            .values_list("charger_id", "total_kw")
        )

        stats: dict[int, dict[str, float]] = {}
        for charger_pk in charger_pks:
            stats[charger_pk] = {
                "total_kw": float(lifetime_totals.get(charger_pk) or 0.0),
                "today_kw": float(today_totals.get(charger_pk) or 0.0),
            }
        return stats

    base_stats = _batch_charger_stats(charger_ids)

    def _charger_stats(charger: Charger, tx_obj=None) -> dict[str, float]:
        cache_key = charger.pk or id(charger)
        if cache_key not in stats_cache:
            stats_cache[cache_key] = {
                "total_kw": float(base_stats.get(charger.pk, {}).get("total_kw", 0.0)),
                "today_kw": float(base_stats.get(charger.pk, {}).get("today_kw", 0.0)),
            }
            if tx_obj and _has_active_session(tx_obj):
                kw_value = getattr(tx_obj, "kw", None)
                if kw_value:
                    stats_cache[cache_key]["total_kw"] += float(kw_value)
                    if _tx_started_within(tx_obj, day_start, day_end):
                        stats_cache[cache_key]["today_kw"] += float(kw_value)
        return stats_cache[cache_key]

    def _status_url(charger: Charger) -> str:
        return _reverse_connector_url(
            "charger-status",
            charger.charger_id,
            charger.connector_slug,
        )

    latest_tx_ids = [
        tx_id
        for tx_id in {getattr(charger, "latest_tx_id", None) for charger in visible_chargers}
        if tx_id
    ]
    latest_tx_map: dict[int, Transaction] = {}
    if latest_tx_ids:
        latest_tx_map = {
            tx.pk: tx
            for tx in Transaction.objects.filter(pk__in=latest_tx_ids)
            .select_related("charger")
        }

    chargers: list[dict[str, object]] = []
    charger_groups: list[dict[str, object]] = []
    group_lookup: dict[str, dict[str, object]] = {}

    for charger in visible_chargers:
        tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
        if not tx_obj:
            tx_obj = latest_tx_map.get(getattr(charger, "latest_tx_id", None))
        has_session = _has_active_session(tx_obj)
        state, color = _charger_state(charger, tx_obj)
        if (
            charger.connector_id is not None
            and not has_session
            and (charger.last_status or "").strip().casefold() == "charging"
        ):
            state_label = force_str(state or "").casefold()
            available_label = force_str(STATUS_BADGE_MAP["available"][0]).casefold()
            if state_label != available_label:
                state, color = STATUS_BADGE_MAP["charging"]
        entry = {
            "charger": charger,
            "state": state,
            "color": color,
            "display_name": _charger_display_name(charger),
            "last_seen": _charger_last_seen(charger),
            "stats": _charger_stats(charger, tx_obj),
            "charging_limit": _charging_limit_details(charger),
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
        connector_states = [
            force_str(child.get("state", "")).strip().casefold()
            for child in group["children"]
            if child["charger"].connector_id is not None
        ]
        charging_state = force_str(STATUS_BADGE_MAP["charging"][0]).casefold()
        if connector_states and all(state == charging_state for state in connector_states):
            label, badge_color = STATUS_BADGE_MAP["charging"]
            parent_entry["state"] = label
            parent_entry["color"] = badge_color
    scheme = "wss" if is_https_request(request) else "ws"
    host = request.get_host()
    ws_url = f"{scheme}://{host}/ocpp/<CHARGE_POINT_ID>/"
    context = {
        "chargers": chargers,
        "charger_groups": charger_groups,
        "show_demo_notice": is_watchtower,
        "demo_ws_url": ws_url,
        "ws_rate_limit": store.MAX_CONNECTIONS_PER_IP,
    }
    wants_table_partial = request.GET.get("partial") == "table"
    accepts_json = "application/json" in request.headers.get("Accept", "").lower()
    if is_htmx or wants_table_partial or request.headers.get("x-requested-with") == "XMLHttpRequest":
        html = render_to_string(
            "ocpp/includes/dashboard_table_rows.html", context, request=request
        )
        if is_htmx or (wants_table_partial and not accepts_json):
            return HttpResponse(html)
        return JsonResponse({"html": html})
    return render(request, "ocpp/dashboard.html", context)
