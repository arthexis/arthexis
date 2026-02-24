"""AWG reporting views."""

from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from django.contrib.auth.decorators import login_required
from django.template.response import TemplateResponse
from django.test import signals as test_signals
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from apps.energy.models import EnergyTariff
from apps.sites.utils import landing


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
    kva = (voltage * current * phase_multiplier / Decimal("1000")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    kw = (kva * power_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    kvar = max(Decimal("0"), (kva * kva) - (kw * kw)).sqrt().quantize(
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
@login_required(login_url="pages:login")
def electrical_power_calculator(request):
    """Estimate kVA, kW, and kVAr from field voltage/current measurements."""

    form_data = request.POST or request.GET
    form = {k: v for k, v in form_data.items() if v not in (None, "", "None")}
    form.setdefault("phases", "1")
    max_input_value = Decimal("1000000000")
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
                error = _("Power factor must be between 0 and 1.")
            elif values["voltage"] > max_input_value or values["current"] > max_input_value:
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
