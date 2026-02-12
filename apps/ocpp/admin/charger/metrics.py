"""Metrics and tariff helper methods for charger admin views."""

from ..common_imports import *


class ChargerMetricsMixin:
    """Provide changelist stats and tariff lookups."""

    def total_kw_display(self, obj):
        return round(obj.total_kw, 2)

    total_kw_display.short_description = "Total kW"

    def today_kw(self, obj):
        start, end = self._today_range()
        return round(obj.total_kw_for_range(start, end), 2)

    today_kw.short_description = "Today kW"

    def changelist_view(self, request, extra_context=None):
        clear_stale_cached_statuses()
        response = super().changelist_view(request, extra_context=extra_context)
        if hasattr(response, "context_data"):
            cl = response.context_data.get("cl")
            if cl is not None:
                response.context_data.update(
                    self._charger_quick_stats_context(cl.queryset)
                )
        return response

    def _charger_quick_stats_context(self, queryset):
        chargers = list(queryset)
        stats = {
            "total_kw": 0.0,
            "today_kw": 0.0,
            "estimated_cost": None,
            "availability_percentage": None,
        }
        if not chargers:
            return {"charger_quick_stats": stats}

        parent_ids = {c.charger_id for c in chargers if c.connector_id is None}
        start, end = self._today_range()
        window_end = timezone.now()
        window_start = window_end - timedelta(hours=24)
        tariff_cache = self._build_tariff_cache(window_end)
        estimated_cost = Decimal("0")
        cost_available = False
        reported_count = 0
        available_count = 0

        for charger in chargers:
            include_totals = True
            if charger.connector_id is not None and charger.charger_id in parent_ids:
                include_totals = False
            if not include_totals:
                continue

            stats["total_kw"] += charger.total_kw
            stats["today_kw"] += charger.total_kw_for_range(start, end)

            energy_window = Decimal(
                str(charger.total_kw_for_range(window_start, window_end))
            )
            price = self._select_tariff_price(
                tariff_cache,
                getattr(charger.location, "zone", None),
                getattr(charger.location, "contract_type", None),
                window_end,
            )
            if price is not None:
                estimated_cost += energy_window * price
                cost_available = True

            availability_state = self._charger_availability_state(charger)
            availability_timestamp = self._charger_availability_timestamp(charger)
            if availability_timestamp and availability_timestamp >= window_start:
                reported_count += 1
                if availability_state.casefold() == "operative":
                    available_count += 1

        stats["total_kw"] = round(stats["total_kw"], 2)
        stats["today_kw"] = round(stats["today_kw"], 2)
        if cost_available:
            stats["estimated_cost"] = estimated_cost.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        if reported_count:
            stats["availability_percentage"] = round(
                (available_count / reported_count) * 100.0, 1
            )

        return {"charger_quick_stats": stats}

    @staticmethod
    def _tariff_active_at(tariff, moment: time) -> bool:
        start = tariff.start_time
        end = tariff.end_time
        if start <= end:
            return start <= moment < end
        return moment >= start or moment < end

    def _build_tariff_cache(self, reference_time: datetime) -> dict[tuple[str | None, str | None], list[EnergyTariff]]:
        tariffs = list(
            EnergyTariff.objects.filter(
                unit=EnergyTariff.Unit.KWH, year__lte=reference_time.year
            ).order_by("-year", "season", "start_time")
        )
        cache: dict[tuple[str | None, str | None], list[EnergyTariff]] = {}
        fallback: list[EnergyTariff] = []
        for tariff in tariffs:
            key = (tariff.zone, tariff.contract_type)
            cache.setdefault(key, []).append(tariff)
            fallback.append(tariff)
        cache[(None, None)] = fallback
        return cache

    def _select_tariff_price(
        self,
        cache: dict[tuple[str | None, str | None], list[EnergyTariff]],
        zone: str | None,
        contract_type: str | None,
        reference_time: datetime,
    ) -> Decimal | None:
        key = (zone or None, contract_type or None)
        candidates = cache.get(key)
        if not candidates:
            candidates = cache.get((None, None), [])
        if not candidates:
            return None
        moment = reference_time.time()
        for tariff in candidates:
            if self._tariff_active_at(tariff, moment):
                return tariff.price_mxn
        return candidates[0].price_mxn

    @staticmethod
    def _charger_availability_state(charger) -> str:
        state = (getattr(charger, "availability_state", "") or "").strip()
        if state:
            return state
        derived = Charger.availability_state_from_status(
            getattr(charger, "last_status", "")
        )
        return derived or ""

    @staticmethod
    def _charger_availability_timestamp(charger):
        timestamp = getattr(charger, "availability_state_updated_at", None)
        if timestamp:
            return timestamp
        return getattr(charger, "last_status_timestamp", None)

    def _today_range(self):
        today = timezone.localdate()
        start = datetime.combine(today, time.min)
        if timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
        end = start + timedelta(days=1)
        return start, end
