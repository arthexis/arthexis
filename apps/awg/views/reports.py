"""AWG reporting views."""

from __future__ import annotations

from collections.abc import MutableMapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from math import comb
from typing import Optional

from django.contrib.auth.decorators import login_required
from django.template.response import TemplateResponse
from django.test import signals as test_signals
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from apps.energy.models import EnergyTariff
from apps.sites.utils import landing

from ..models import HypergeometricTemplate

MAX_POWER_CALCULATOR_INPUT = Decimal("1000000000")
MAX_HYPERGEOMETRIC_INPUT = 500
MTG_PROBABILITY_THRESHOLDS = {
    0.8: "draws_to_80_percent",
    0.9: "draws_to_90_percent",
    0.99: "draws_to_99_percent",
}


def _format_decimal(value: Decimal, places: str = "0.0000") -> Decimal:
    """Return ``value`` quantized to the requested decimal ``places``."""

    quantizer = Decimal(places)
    return value.quantize(quantizer, rounding=ROUND_HALF_UP)


def _prepare_energy_tariff_options(
    form: MutableMapping[str, str]
) -> tuple[dict[str, object], dict[str, EnergyTariff], list[EnergyTariff]]:
    """Return selection metadata and tariff choices for the energy calculator."""

    base_qs = EnergyTariff.objects.filter(unit=EnergyTariff.Unit.KWH)
    years = sorted(base_qs.values_list("year", flat=True).distinct(), reverse=True)

    context: dict[str, object] = {"years": years}

    if years and "year" not in form:
        form["year"] = str(years[0])

    tariffs_year = base_qs
    year_value: Optional[int] = None
    if "year" in form:
        try:
            year_value = int(form["year"])
        except (TypeError, ValueError):
            form.pop("year", None)
        else:
            tariffs_year = tariffs_year.filter(year=year_value)

    contract_values = sorted(
        tariffs_year.values_list("contract_type", flat=True).distinct()
    )
    context["contract_options"] = [
        {"value": value, "label": EnergyTariff.ContractType(value).label}
        for value in contract_values
    ]

    tariffs_contract = tariffs_year
    contract_type = form.get("contract_type")
    if contract_type:
        tariffs_contract = tariffs_contract.filter(contract_type=contract_type)

    zone_values = sorted(tariffs_contract.values_list("zone", flat=True).distinct())
    context["zone_options"] = zone_values

    tariffs_zone = tariffs_contract
    zone = form.get("zone")
    if zone:
        tariffs_zone = tariffs_zone.filter(zone=zone)

    season_values = sorted(tariffs_zone.values_list("season", flat=True).distinct())
    context["season_options"] = [
        {"value": value, "label": EnergyTariff.Season(value).label}
        for value in season_values
    ]

    tariffs_season = tariffs_zone
    season = form.get("season")
    if season:
        tariffs_season = tariffs_season.filter(season=season)

    period_values = sorted(
        tariffs_season.values_list("period", flat=True).distinct()
    )
    context["period_options"] = [
        {"value": value, "label": EnergyTariff.Period(value).label}
        for value in period_values
    ]

    tariffs_period = tariffs_season
    period = form.get("period")
    if period:
        tariffs_period = tariffs_period.filter(period=period)

    tariff_list = list(
        tariffs_period.order_by(
            "contract_type", "zone", "season", "period", "start_time"
        )
    )

    context["tariff_options"] = [
        {
            "id": str(t.pk),
            "label": _(
                "%(contract)s • Zone %(zone)s • %(season)s • %(period)s (%(start)s – %(end)s) @ %(price)s MXN/kWh"
            )
            % {
                "contract": t.get_contract_type_display(),
                "zone": t.zone,
                "season": t.get_season_display(),
                "period": t.get_period_display(),
                "start": t.start_time.strftime("%H:%M"),
                "end": t.end_time.strftime("%H:%M"),
                "price": _format_decimal(t.price_mxn),
            },
        }
        for t in tariff_list
    ]
    context["no_tariffs"] = not tariff_list

    tariff_map = {str(t.pk): t for t in tariff_list}
    return context, tariff_map, tariff_list


def _calculate_energy_tariff_totals(
    *, tariff: EnergyTariff, kwh: Decimal
) -> dict[str, Decimal]:
    """Return the billing totals for the provided ``tariff`` and ``kwh``."""

    kwh_display = _format_decimal(kwh, "0.01")
    unit_price = _format_decimal(tariff.price_mxn)
    unit_cost = _format_decimal(tariff.cost_mxn)
    total_price = (kwh_display * unit_price).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total_cost = (kwh_display * unit_cost).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    margin_total = (total_price - total_cost).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "kwh": kwh_display,
        "unit_price": unit_price,
        "unit_cost": unit_cost,
        "total_price": total_price,
        "total_cost": total_cost,
        "margin_total": margin_total,
    }


def _calculate_power_totals(
    *, voltage: Decimal, current: Decimal, power_factor: Decimal, phases: str
) -> dict[str, Decimal]:
    """Return key power-triangle values for electrician sizing checks."""

    phase_multiplier = Decimal("3").sqrt() if phases == "3" else Decimal("1")
    kva_raw = voltage * current * phase_multiplier / Decimal("1000")
    kw_raw = kva_raw * power_factor
    kvar_raw = max(Decimal("0"), (kva_raw * kva_raw) - (kw_raw * kw_raw)).sqrt()

    kva = kva_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    kw = kw_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    kvar = kvar_raw.quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    recommended_breaker = (current * Decimal("1.25")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {
        "kva": kva,
        "kw": kw,
        "kvar": kvar,
        "recommended_breaker": recommended_breaker,
    }


def _calculate_ev_charging_totals(
    *,
    battery_kwh: Decimal,
    start_soc: Decimal,
    target_soc: Decimal,
    charger_power_kw: Decimal,
    charging_efficiency: Decimal,
    tariff_mxn_kwh: Optional[Decimal],
) -> dict[str, Decimal]:
    """Return EV charging energy, duration, and cost estimation totals."""

    soc_delta = (target_soc - start_soc) / Decimal("100")
    battery_energy_needed = (battery_kwh * soc_delta).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    wall_energy_needed = (battery_energy_needed / charging_efficiency).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    charging_time_hours = (wall_energy_needed / charger_power_kw).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    totals: dict[str, Decimal] = {
        "battery_energy_needed": battery_energy_needed,
        "wall_energy_needed": wall_energy_needed,
        "charging_time_hours": charging_time_hours,
    }
    if tariff_mxn_kwh is not None:
        totals["tariff_mxn_kwh"] = tariff_mxn_kwh.quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        totals["estimated_cost_mxn"] = (wall_energy_needed * tariff_mxn_kwh).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return totals


def _hypergeometric_probability(
    *,
    population_size: int,
    success_states: int,
    draws: int,
    successes_drawn: int,
) -> float:
    """Return P(X = k) for a hypergeometric distribution."""

    if not 0 <= successes_drawn <= draws:
        return 0.0
    if successes_drawn > success_states or draws - successes_drawn > (
        population_size - success_states
    ):
        return 0.0

    numerator = comb(success_states, successes_drawn) * comb(
        population_size - success_states, draws - successes_drawn
    )
    denominator = comb(population_size, draws)
    return numerator / denominator


def _draws_for_probability_thresholds(
    *,
    deck_size: int,
    success_states: int,
    thresholds: tuple[float, ...],
) -> dict[float, int | None]:
    """Return minimum draws needed to reach each probability threshold."""

    if success_states <= 0:
        return dict.fromkeys(thresholds)

    draws_needed: dict[float, int | None] = dict.fromkeys(thresholds)
    remaining = set(thresholds)
    for draw_count in range(1, min(deck_size, MAX_HYPERGEOMETRIC_INPUT) + 1):
        probability_none = _hypergeometric_probability(
            population_size=deck_size,
            success_states=success_states,
            draws=draw_count,
            successes_drawn=0,
        )
        probability_any = 1 - probability_none
        met = {threshold for threshold in remaining if probability_any >= threshold}
        for threshold in met:
            draws_needed[threshold] = draw_count
        remaining -= met
        if not remaining:
            break

    return draws_needed


def _calculate_hypergeometric_totals(
    *,
    deck_size: int,
    success_states: int,
    draws: int,
    min_successes: int,
    exact_successes: Optional[int],
) -> dict[str, float]:
    """Return probability totals used by the MTG hypergeometric calculator."""

    probability_exact = (
        _hypergeometric_probability(
            population_size=deck_size,
            success_states=success_states,
            draws=draws,
            successes_drawn=exact_successes,
        )
        if exact_successes is not None
        else None
    )

    max_possible_successes = min(draws, success_states)
    probability_at_least = sum(
        _hypergeometric_probability(
            population_size=deck_size,
            success_states=success_states,
            draws=draws,
            successes_drawn=value,
        )
        for value in range(min_successes, max_possible_successes + 1)
    )
    probability_none = _hypergeometric_probability(
        population_size=deck_size,
        success_states=success_states,
        draws=draws,
        successes_drawn=0,
    )
    probability_any = 1 - probability_none
    draws_to_high_chance = _draws_for_probability_thresholds(
        deck_size=deck_size,
        success_states=success_states,
        thresholds=tuple(MTG_PROBABILITY_THRESHOLDS),
    )

    return {
        "probability_exact": probability_exact,
        "probability_at_least": probability_at_least,
        "probability_none": probability_none,
        "probability_any": probability_any,
        **{
            result_key: draws_to_high_chance[threshold]
            for threshold, result_key in MTG_PROBABILITY_THRESHOLDS.items()
        },
    }


@landing(_lazy("Energy Tariff Calculator"))
@login_required(login_url="pages:login")
def energy_tariff_calculator(request):
    """Estimate MXN costs for a given kWh consumption using CFE tariffs."""

    form_data = request.POST or request.GET
    form = {k: v for k, v in form_data.items() if v not in (None, "", "None")}

    context: dict[str, object] = {"form": form}
    options_context, tariff_map, tariff_list = _prepare_energy_tariff_options(form)
    context.update(options_context)

    if request.method == "GET" and tariff_list and "tariff" not in form:
        form["tariff"] = str(tariff_list[0].pk)

    if request.method == "POST":
        error: Optional[str] = None
        kwh_str = request.POST.get("kwh")
        tariff_id = request.POST.get("tariff")

        if not kwh_str:
            error = _("Enter the energy consumption in kWh.")
        if not error:
            if not tariff_id:
                error = _("Select an energy tariff to continue.")
            elif tariff_id not in tariff_map:
                error = _("Selected tariff is not available for the chosen filters.")

        kwh_value: Optional[Decimal] = None
        if not error and kwh_str is not None:
            try:
                kwh_value = Decimal(kwh_str)
            except (InvalidOperation, TypeError):
                error = _("Energy consumption must be a number.")
            else:
                if kwh_value <= 0:
                    error = _("Energy consumption must be greater than zero.")

        if error:
            context["error"] = error
        else:
            assert kwh_value is not None and tariff_id is not None
            selected = tariff_map[tariff_id]
            totals = _calculate_energy_tariff_totals(
                tariff=selected, kwh=kwh_value
            )
            context["result"] = {"tariff": selected, **totals}
            form["kwh"] = str(totals["kwh"])

    response = TemplateResponse(request, "awg/energy_tariff_calculator.html", context)

    template = response.resolve_template(response.template_name)
    response.add_post_render_callback(lambda r: setattr(r, "context", context))
    response.render()

    # The Django Debug Toolbar expects template objects to expose a ``name`` attribute.
    # ``django.template.backends.django.Template`` proxies the underlying template via
    # its ``template`` attribute, so unwrap it when available before emitting the
    # ``template_rendered`` signal.
    signal_template = getattr(template, "template", template)
    test_signals.template_rendered.send(
        sender=signal_template.__class__,
        template=signal_template,
        context=context,
        request=request,
    )
    return response


@landing(_lazy("Electrical Power Calculator"))
def electrical_power_calculator(request):
    """Estimate kVA, kW, and kVAr from field voltage/current measurements."""

    form_data = request.POST or request.GET
    form = {k: v for k, v in form_data.items() if v not in (None, "", "None")}
    form.setdefault("phases", "1")
    context: dict[str, object] = {"form": form}

    if request.method == "POST":
        error: Optional[str] = None
        fields = {
            "voltage": request.POST.get("voltage"),
            "current": request.POST.get("current"),
            "power_factor": request.POST.get("power_factor"),
            "phases": request.POST.get("phases", "1"),
        }

        for required in ("voltage", "current", "power_factor"):
            if not fields[required]:
                error = _("%(field)s is required.") % {
                    "field": required.replace("_", " ").title()
                }
                break

        values: dict[str, Decimal] = {}
        if not error:
            try:
                values["voltage"] = Decimal(fields["voltage"] or "0")
                values["current"] = Decimal(fields["current"] or "0")
                values["power_factor"] = Decimal(fields["power_factor"] or "0")
            except (InvalidOperation, TypeError):
                error = _("Voltage, current, and power factor must be numbers.")

        if not error:
            if not all(value.is_finite() for value in values.values()):
                error = _("Voltage, current, and power factor must be finite numbers.")
            elif values["voltage"] <= 0 or values["current"] <= 0:
                error = _("Voltage and current must be greater than zero.")
            elif values["power_factor"] <= 0 or values["power_factor"] > 1:
                error = _("Power factor must be greater than 0 and at most 1.")
            elif (
                values["voltage"] > MAX_POWER_CALCULATOR_INPUT
                or values["current"] > MAX_POWER_CALCULATOR_INPUT
            ):
                error = _("Voltage and current are too large to calculate safely.")
            elif fields["phases"] not in {"1", "3"}:
                error = _("Phases must be either 1 or 3.")

        if error:
            context["error"] = error
        else:
            try:
                totals = _calculate_power_totals(
                    voltage=values["voltage"],
                    current=values["current"],
                    power_factor=values["power_factor"],
                    phases=fields["phases"],
                )
            except InvalidOperation:
                context["error"] = _(
                    "Unable to calculate power totals for the provided values."
                )
            else:
                context["result"] = totals
                form["voltage"] = str(values["voltage"])
                form["current"] = str(values["current"])
                form["power_factor"] = str(values["power_factor"])

    response = TemplateResponse(request, "awg/electrical_power_calculator.html", context)

    template = response.resolve_template(response.template_name)
    response.add_post_render_callback(lambda r: setattr(r, "context", context))
    response.render()

    signal_template = getattr(template, "template", template)
    test_signals.template_rendered.send(
        sender=signal_template.__class__,
        template=signal_template,
        context=context,
        request=request,
    )
    return response


@landing(_lazy("EV Charging Session Calculator"))
def ev_charging_calculator(request):
    """Estimate EV charging time and energy cost for a target SOC window."""

    form_data = request.POST or request.GET
    form = {k: v for k, v in form_data.items() if v not in (None, "", "None")}
    form.setdefault("charging_efficiency", "0.90")
    context: dict[str, object] = {"form": form}

    if request.method == "POST":
        error: Optional[str] = None
        fields = {
            "battery_kwh": request.POST.get("battery_kwh"),
            "start_soc": request.POST.get("start_soc"),
            "target_soc": request.POST.get("target_soc"),
            "charger_power_kw": request.POST.get("charger_power_kw"),
            "charging_efficiency": request.POST.get("charging_efficiency", "0.90"),
            "tariff_mxn_kwh": request.POST.get("tariff_mxn_kwh"),
        }

        for required in (
            "battery_kwh",
            "start_soc",
            "target_soc",
            "charger_power_kw",
            "charging_efficiency",
        ):
            if not fields[required]:
                error = _("%(field)s is required.") % {
                    "field": required.replace("_", " ").title()
                }
                break

        values: dict[str, Decimal] = {}
        if not error:
            try:
                values["battery_kwh"] = Decimal(fields["battery_kwh"] or "0")
                values["start_soc"] = Decimal(fields["start_soc"] or "0")
                values["target_soc"] = Decimal(fields["target_soc"] or "0")
                values["charger_power_kw"] = Decimal(fields["charger_power_kw"] or "0")
                values["charging_efficiency"] = Decimal(
                    fields["charging_efficiency"] or "0"
                )
                if fields["tariff_mxn_kwh"]:
                    values["tariff_mxn_kwh"] = Decimal(fields["tariff_mxn_kwh"])
            except (InvalidOperation, TypeError):
                error = _("All numeric fields must be valid numbers.")

        if not error:
            if not all(value.is_finite() for value in values.values()):
                error = _("All numeric fields must be finite numbers.")
            elif values["battery_kwh"] <= 0:
                error = _("Battery capacity must be greater than zero.")
            elif values["charger_power_kw"] <= 0:
                error = _("Charger power must be greater than zero.")
            elif values["start_soc"] < 0 or values["target_soc"] > 100:
                error = _("State of charge must stay between 0 and 100 percent.")
            elif values["target_soc"] <= values["start_soc"]:
                error = _("Target SOC must be greater than start SOC.")
            elif (
                values["charging_efficiency"] <= 0
                or values["charging_efficiency"] > 1
            ):
                error = _("Charging efficiency must be greater than 0 and at most 1.")
            elif "tariff_mxn_kwh" in values and values["tariff_mxn_kwh"] < 0:
                error = _("Tariff must be zero or greater.")

        if error:
            context["error"] = error
        else:
            try:
                totals = _calculate_ev_charging_totals(
                    battery_kwh=values["battery_kwh"],
                    start_soc=values["start_soc"],
                    target_soc=values["target_soc"],
                    charger_power_kw=values["charger_power_kw"],
                    charging_efficiency=values["charging_efficiency"],
                    tariff_mxn_kwh=values.get("tariff_mxn_kwh"),
                )
            except (InvalidOperation, ZeroDivisionError):
                context["error"] = _(
                    "Unable to calculate EV charging totals for the provided values."
                )
            else:
                context["result"] = totals
                for field, value in fields.items():
                    if value:
                        form[field] = value

    response = TemplateResponse(request, "awg/ev_charging_calculator.html", context)

    template = response.resolve_template(response.template_name)
    response.add_post_render_callback(lambda r: setattr(r, "context", context))
    response.render()

    signal_template = getattr(template, "template", template)
    test_signals.template_rendered.send(
        sender=signal_template.__class__,
        template=signal_template,
        context=context,
        request=request,
    )
    return response


@landing(_lazy("MTG Hypergeometric Calculator"))
def mtg_hypergeometric_calculator(request):
    """Estimate opening-hand draw odds for Magic: The Gathering deckbuilding."""

    form_data = request.POST or request.GET
    form = {k: v for k, v in form_data.items() if v not in (None, "", "None")}
    form.setdefault("deck_size", "60")
    form.setdefault("success_states", "4")
    form.setdefault("draws", "7")
    form.setdefault("min_successes", "1")

    templates = HypergeometricTemplate.objects.filter(show_in_pages=True).order_by(
        "name"
    )
    context: dict[str, object] = {"form": form, "templates": templates}

    if request.method == "POST":
        error: Optional[str] = None
        fields = {
            "deck_size": request.POST.get("deck_size"),
            "success_states": request.POST.get("success_states"),
            "draws": request.POST.get("draws"),
            "min_successes": request.POST.get("min_successes"),
            "exact_successes": request.POST.get("exact_successes"),
        }
        parsed_values: dict[str, int] = {}

        for required in ("deck_size", "success_states", "draws", "min_successes"):
            if not fields[required]:
                error = _("%(field)s is required.") % {
                    "field": required.replace("_", " ").title()
                }
                break

        if not error:
            try:
                parsed_values["deck_size"] = int(fields["deck_size"] or "0")
                parsed_values["success_states"] = int(fields["success_states"] or "0")
                parsed_values["draws"] = int(fields["draws"] or "0")
                parsed_values["min_successes"] = int(fields["min_successes"] or "0")
                if fields["exact_successes"]:
                    parsed_values["exact_successes"] = int(fields["exact_successes"])
            except (TypeError, ValueError):
                error = _("All inputs must be whole numbers.")

        if not error:
            deck_size = parsed_values["deck_size"]
            success_states = parsed_values["success_states"]
            draws = parsed_values["draws"]
            min_successes = parsed_values["min_successes"]
            exact_successes = parsed_values.get("exact_successes")

            if deck_size <= 0:
                error = _("Deck size must be greater than zero.")
            elif deck_size > MAX_HYPERGEOMETRIC_INPUT:
                error = _("Deck size must be %(max_value)s or less.") % {
                    "max_value": MAX_HYPERGEOMETRIC_INPUT
                }
            elif success_states < 0:
                error = _("Success states cannot be negative.")
            elif success_states > deck_size:
                error = _("Success states cannot exceed deck size.")
            elif draws <= 0:
                error = _("Draw count must be greater than zero.")
            elif draws > deck_size:
                error = _("Draw count cannot exceed deck size.")
            elif draws > MAX_HYPERGEOMETRIC_INPUT:
                error = _("Draw count must be %(max_value)s or less.") % {
                    "max_value": MAX_HYPERGEOMETRIC_INPUT
                }
            elif min_successes < 0:
                error = _("Minimum successes cannot be negative.")
            elif min_successes > draws:
                error = _("Minimum successes cannot exceed draws.")
            elif exact_successes is not None and (
                exact_successes < 0 or exact_successes > draws
            ):
                error = _("Exact successes must be between 0 and draws.")
            elif exact_successes is not None and exact_successes > success_states:
                error = _("Exact successes cannot exceed success states.")

        if error:
            context["error"] = error
        else:
            context["result"] = _calculate_hypergeometric_totals(
                deck_size=parsed_values["deck_size"],
                success_states=parsed_values["success_states"],
                draws=parsed_values["draws"],
                min_successes=parsed_values["min_successes"],
                exact_successes=parsed_values.get("exact_successes"),
            )
            for field, value in fields.items():
                if value not in (None, "", "None"):
                    form[field] = value

    return TemplateResponse(request, "awg/mtg_hypergeometric_calculator.html", context)
