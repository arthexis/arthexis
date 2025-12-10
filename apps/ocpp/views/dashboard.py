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
from django.utils.translation import gettext_lazy as _

from apps.nodes.models import Node
from apps.pages.utils import landing

from .. import store
from ..models import Charger, Transaction, annotate_transaction_energy_bounds
from ..status_display import STATUS_BADGE_MAP
from . import common as view_common
from .common import (
    _aggregate_dashboard_state,
    _charger_state,
    _clear_stale_statuses_for_view,
    _connector_overview,
)


@landing("CPMS Online Dashboard")
def dashboard(request):
    """Landing page listing all known chargers and their status."""
    is_htmx = request.headers.get("HX-Request") == "true"
    _clear_stale_statuses_for_view()
    node = Node.get_local()
    role = node.role if node else None
    role_name = role.name if role else ""
    allow_anonymous_roles = {"Watchtower", "Constellation", "Satellite"}
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
            return start <= timezone.make_aware(start_time, tz) < end
        return start <= start_time < end

    def _today_stats(charger: Charger) -> tuple[float, float]:
        if not charger.pk:
            return 0.0, 0.0
        cached = stats_cache.get(charger.pk)
        if cached:
            return cached.get("energy") or 0.0, cached.get("hours") or 0.0
        today_energy = (
            annotate_transaction_energy_bounds(
                Transaction.objects.filter(
                    charger=charger, start_time__gte=day_start, start_time__lt=day_end
                )
            )
            .annotate(
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
            .aggregate(total_energy=Sum("kw_value"))["total_energy"]
        )
        total_seconds = Transaction.objects.filter(
            charger=charger,
            start_time__gte=day_start,
            start_time__lt=day_end,
            stop_time__isnull=False,
        ).aggregate(
            total_seconds=Sum(
                ExpressionWrapper(
                    F("stop_time") - F("start_time"), output_field=FloatField()
                )
            )
        )["total_seconds"]
        hours = (total_seconds or 0.0) / 3600.0
        stats_cache[charger.pk] = {"energy": today_energy or 0.0, "hours": hours}
        return today_energy or 0.0, hours

    chargers: list[dict[str, object]] = []
    charger_groups: dict[str, dict] = {}
    for charger in visible_chargers:
        connector_slug = charger.connector_slug
        cid = charger.charger_id
        tx_obj = None
        if charger.latest_tx_id:
            tx_obj = (
                Transaction.objects.filter(pk=charger.latest_tx_id)
                .select_related("charger")
                .first()
            )
        state, color = _charger_state(charger, tx_obj)
        tx_started_today = _tx_started_within(tx_obj, day_start, day_end)
        today_energy, hours = _today_stats(charger)
        entries = []
        if not connector_slug or connector_slug == Charger.AGGREGATE_CONNECTOR_SLUG:
            entries = _connector_overview(charger, request.user, connectors=visible_chargers)
        charger_entry = {
            "charger": charger,
            "entry_key": _reverse_connector_url(
                "charger-page", cid, charger.connector_slug
            ),
            "connector": connector_slug or "",
            "name": _charger_display_name(charger),
            "state": state,
            "color": color,
            "tx_started_today": tx_started_today,
            "today_energy": today_energy,
            "today_hours": hours,
            "entries": entries,
            "last_seen": charger.last_seen,
            "connected": store.is_connected(cid, charger.connector_id),
            "location": charger.location,
        }
        if is_watchtower:
            charger_entry["state"] = _aggregate_dashboard_state(charger) or (
                _("Unknown"),
                "gray",
            )
            charger_entry["color"] = _aggregate_dashboard_state(charger)[1]
        chargers.append(charger_entry)
        group_key = (charger.location.name if charger.location else "Unknown").lower()
        if group_key not in charger_groups:
            charger_groups[group_key] = {
                "key": group_key,
                "name": charger.location.name if charger.location else _("Unknown"),
                "entries": [],
                "sibling_states": [],
                "sibling_transaction": 0,
                "sibling_error": 0,
            }
        charger_groups[group_key]["entries"].append(charger_entry)
        if tx_started_today:
            charger_groups[group_key]["sibling_transaction"] += 1
        badge_status = _aggregate_dashboard_state(charger)
        if badge_status is not None:
            badge_color = badge_status[1]
            badge_state = badge_status[0]
            parent_entry = charger_groups[group_key]
            parent_entry["sibling_states"].append(badge_state)
            if badge_color == "#dc3545":
                parent_entry["sibling_error"] += 1
            if len(parent_entry["sibling_states"]) == 1:
                parent_entry["state"] = badge_state
                parent_entry["color"] = badge_color
            else:
                parent_entry["state"] = (
                    _aggregate_dashboard_state(charger)[0]
                    if badge_state == _aggregate_dashboard_state(charger)[0]
                    else _("Mixed")
                )
                parent_entry["color"] = badge_color
        if not charger_groups[group_key].get("state"):
            label, badge_color = STATUS_BADGE_MAP.get("unknown", (_("Unknown"), "#adb5bd"))
            parent_entry = charger_groups[group_key]
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
