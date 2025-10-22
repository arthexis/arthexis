"""AWG calculator views and utilities."""

from __future__ import annotations

import ipaddress
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Literal, Optional, Union

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from pages.utils import landing

from .models import (
    CableSize,
    ConduitFill,
    CalculatorTemplate,
    EnergyTariff,
    PowerLead,
)

from .constants import CONDUIT_LABELS


class AWG(int):
    """Represents an AWG gauge as an integer.
    Positive numbers are thin wires (e.g., 14),
    while zero and negative numbers use zero notation ("1/0", "2/0", ...).
    """

    def __new__(cls, value):  # pragma: no cover - simple parsing
        if isinstance(value, str) and "/" in value:
            value = -int(value.split("/")[0])
        return super().__new__(cls, int(value))

    def __str__(self):  # pragma: no cover - trivial
        return f"{abs(self)}/0" if self < 0 else str(int(self))


def _fill_field(size: Union[str, int]) -> str:
    """Return the ConduitFill field name for an AWG size."""

    n = int(AWG(size))
    return "awg_" + ("0" * (-n) if n < 0 else str(n))


def _display_awg(size: Union[str, int]) -> str:
    """Return an AWG display string preferring even numbers when possible."""

    n = int(AWG(size))
    if n > 0 and n % 2:
        return f"{n - 1}-{n}"
    return str(AWG(n))


def _parse_ground(value: Union[str, int, None]) -> tuple[int, str]:
    """Return the numeric ground count and any special label."""

    if value in (None, "", "None"):
        return 0, ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "[1]":
            return 1, "[1]"
        value = stripped
    return int(value), ""


def _format_ground_output(amount: int, label: str) -> str:
    """Return a formatted ground string including any special label."""

    return f"{amount} ({label})" if label else str(amount)


def _format_decimal(value: Decimal, places: str = "0.0000") -> Decimal:
    """Return ``value`` quantized to the requested decimal ``places``."""

    quantizer = Decimal(places)
    return value.quantize(quantizer, rounding=ROUND_HALF_UP)


def find_conduit(awg: Union[str, int], cables: int, *, conduit: str = "emt"):
    """Return the conduit trade size capable of holding *cables* wires."""

    awg = AWG(awg)
    field = _fill_field(awg)
    qs = (
        ConduitFill.objects.filter(conduit__iexact=conduit)
        .exclude(**{field: None})
        .filter(**{f"{field}__gte": cables})
    )
    rows = list(qs.values_list("trade_size", field))
    if not rows:
        return {"size_inch": "n/a"}

    def _to_float(value: str) -> float:
        total = 0.0
        for part in value.split():
            if "/" in part:
                num, den = part.split("/")
                total += float(num) / float(den)
            else:
                total += float(part)
        return total

    rows.sort(key=lambda r: _to_float(r[0]))
    size, capacity = rows[0]
    if capacity == cables and len(rows) > 1:
        size = rows[1][0]
    return {"size_inch": size}


def find_awg(
    *,
    meters: Union[int, str, None] = None,  # Required
    amps: Union[int, str] = "40",
    volts: Union[int, str] = "220",
    material: Literal["cu", "al", "?"] = "cu",
    max_awg: Optional[Union[int, str]] = None,
    max_lines: Union[int, str] = "1",
    phases: Union[str, int] = "2",
    temperature: Union[int, str, None] = None,
    conduit: Optional[Union[str, bool]] = None,
    ground: Union[int, str] = "1",
):
    """Calculate the cable size required for given parameters.

    This function mirrors the behaviour of the original ``projects.awg`` module,
    but utilises Django's ORM instead of raw SQL.
    """

    amps = int(amps)
    meters = int(meters)
    volts = int(volts)
    max_lines = 1 if max_lines in (None, "") else int(max_lines)
    max_awg = None if max_awg in (None, "") else AWG(max_awg)
    phases = int(phases)
    temperature = None if temperature in (None, "", "auto") else int(temperature)
    ground_value, ground_label = _parse_ground(ground)
    ground_options = [ground_value]
    if ground_label == "[1]":
        ground_options = [1, 0]

    assert amps >= 10, _(
        "Minimum load for this calculator is 15 Amps.  Yours: amps=%(amps)s."
    ) % {"amps": amps}
    assert (amps <= 546) if material == "cu" else (amps <= 430), _(
        "Max. load allowed is 546 A (cu) or 430 A (al). Yours: amps=%(amps)s material=%(material)s"
    ) % {"amps": amps, "material": material}
    assert meters >= 1, _("Consider at least 1 meter of cable.")
    assert 110 <= volts <= 460, _(
        "Volt range supported must be between 110-460. Yours: volts=%(volts)s"
    ) % {"volts": volts}
    assert material in ("cu", "al"), _(
        "Material must be 'cu' (copper) or 'al' (aluminum)."
    )
    assert phases in (1, 2, 3), _(
        "AC phases 1, 2 or 3 to calculate for. DC not supported."
    )
    if temperature is not None:
        assert temperature in (60, 75, 90), _("Temperature must be 60, 75 or 90")

    def solve_for_ground(ground_count: int):
        def _calc(*, force_awg=None, limit_awg=None):
            qs = CableSize.objects.filter(material=material, line_num__lte=max_lines)
            awg_data: dict[int, dict[int, dict[str, float]]] = {}
            for row in qs.values_list(
                "awg_size", "line_num", "k_ohm_km", "amps_60c", "amps_75c", "amps_90c"
            ):
                awg_size, line_num, k_ohm, a60, a75, a90 = row
                awg_int = int(AWG(awg_size))
                if force_awg is not None and awg_int != int(AWG(force_awg)):
                    continue
                if limit_awg is not None and awg_int > int(AWG(limit_awg)):
                    continue
                awg_data.setdefault(awg_int, {})[int(line_num)] = {
                    "k": k_ohm,
                    "a60": a60,
                    "a75": a75,
                    "a90": a90,
                }

            if phases in (2, 3):
                base_vdrop = math.sqrt(3) * meters * amps / 1000
            else:
                base_vdrop = 2 * meters * amps / 1000

            best = None
            best_perc = 1e9

            if force_awg is not None:
                sizes = [int(AWG(force_awg))] if int(AWG(force_awg)) in awg_data else []
            elif limit_awg is None:
                sizes = sorted(awg_data.keys(), reverse=True)
            else:
                sizes = sorted(
                    [s for s in awg_data.keys() if s <= int(AWG(limit_awg))],
                    reverse=True,
                )

            for awg_size in sizes:
                base = awg_data[awg_size][1]
                for n in range(1, max_lines + 1):
                    info = awg_data[awg_size].get(n)
                    a60 = (info or base)["a60"] if info else base["a60"] * n
                    a75 = (info or base)["a75"] if info else base["a75"] * n
                    a90 = (info or base)["a90"] if info else base["a90"] * n
                    if temperature is None:
                        allowed = (a75 >= amps and amps > 100) or (
                            a60 >= amps and amps <= 100
                        )
                    else:
                        tmap = {60: a60, 75: a75, 90: a90}
                        allowed = tmap.get(temperature, 0) >= amps
                    if not allowed and force_awg is None:
                        continue

                    vdrop = base_vdrop * base["k"] / n
                    perc = vdrop / volts
                    awg_str = str(AWG(awg_size))
                    ground_total = n * ground_count
                    result = {
                        "awg": awg_str,
                        "awg_display": _display_awg(awg_size),
                        "meters": meters,
                        "amps": amps,
                        "volts": volts,
                        "temperature": (
                            temperature
                            if temperature is not None
                            else (60 if amps <= 100 else 75)
                        ),
                        "lines": n,
                        "vdrop": vdrop,
                        "vend": volts - vdrop,
                        "vdperc": perc * 100,
                        "cables": f"{n * phases}+{_format_ground_output(ground_total, ground_label)}",
                        "total_meters": f"{n * phases * meters}+{_format_ground_output(meters * ground_total, ground_label)}",
                    }
                    if force_awg is None:
                        if allowed and perc <= 0.03:
                            if conduit:
                                c = "emt" if conduit is True else conduit
                                fill = find_conduit(
                                    AWG(awg_size),
                                    n * (phases + ground_count),
                                    conduit=c,
                                )
                                result["conduit"] = c
                                result["conduit_label"] = CONDUIT_LABELS.get(
                                    str(c).lower(), str(c).upper()
                                )
                                result["pipe_inch"] = fill["size_inch"]
                            return result
                        if perc < best_perc:
                            best = result
                            best_perc = perc
                    else:
                        if allowed and perc <= 0.03:
                            if conduit:
                                c = "emt" if conduit is True else conduit
                                fill = find_conduit(
                                    AWG(awg_size),
                                    n * (phases + ground_count),
                                    conduit=c,
                                )
                                result["conduit"] = c
                                result["conduit_label"] = CONDUIT_LABELS.get(
                                    str(c).lower(), str(c).upper()
                                )
                                result["pipe_inch"] = fill["size_inch"]
                            return result
                        if perc < best_perc:
                            best = result
                            best_perc = perc

            if best and (force_awg is not None or limit_awg is not None):
                if force_awg is not None:
                    best["warning"] = _(
                        "Voltage drop may exceed 3% with chosen parameters"
                    )
                else:
                    best["warning"] = _("Voltage drop exceeds 3% with given max_awg")
                if conduit:
                    c = "emt" if conduit is True else conduit
                    fill = find_conduit(
                        AWG(best["awg"]),
                        best["lines"] * (phases + ground_count),
                        conduit=c,
                    )
                    best["conduit"] = c
                    best["conduit_label"] = CONDUIT_LABELS.get(
                        str(c).lower(), str(c).upper()
                    )
                    best["pipe_inch"] = fill["size_inch"]
                return best

            return {"awg": "n/a", "awg_display": "n/a"}

        baseline = _calc()
        if max_awg is None:
            return baseline

        if baseline.get("awg") == "n/a":
            return _calc(limit_awg=max_awg)

        if int(AWG(baseline["awg"])) < int(max_awg):
            return _calc(force_awg=max_awg)
        return _calc(limit_awg=max_awg)

    results = [(g, solve_for_ground(g)) for g in ground_options]
    if len(results) == 1:
        return results[0][1]

    vd_results = [item for item in results if "vdperc" in item[1]]
    if vd_results:
        worst = max(vd_results, key=lambda item: item[1]["vdperc"])
        return worst[1]

    return results[0][1]


@csrf_exempt
@landing(_lazy("AWG Cable Calculator"))
def calculator(request):
    """Display the AWG calculator form and results using a template."""
    def _extract_client_ip() -> str | None:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        candidates = [value.strip() for value in forwarded.split(",") if value.strip()]
        remote = request.META.get("REMOTE_ADDR", "").strip()
        if remote:
            candidates.append(remote)

        for candidate in candidates:
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate

        return None
    form_data = request.POST or request.GET
    form = {k: v for k, v in form_data.items() if v not in (None, "", "None")}
    if request.GET:
        defaults = {
            "amps": "40",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "2",
            "ground": "1",
        }
        for key, value in defaults.items():
            form.setdefault(key, value)
    context: dict[str, object] = {"form": form}
    if request.method == "POST" and request.POST.get("meters"):
        lead_values = {
            k: v for k, v in request.POST.items() if k != "csrfmiddlewaretoken"
        }
        PowerLead.objects.create(
            user=request.user if request.user.is_authenticated else None,
            values=lead_values,
            path=request.path,
            referer=request.META.get("HTTP_REFERER", ""),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            ip_address=_extract_client_ip(),
        )
        max_awg = request.POST.get("max_awg") or None
        conduit_field = request.POST.get("conduit")
        conduit_arg = None if conduit_field in (None, "") else conduit_field
        try:
            result = find_awg(
                meters=request.POST.get("meters"),
                amps=request.POST.get("amps"),
                volts=request.POST.get("volts"),
                material=request.POST.get("material"),
                max_lines=request.POST.get("max_lines"),
                phases=request.POST.get("phases"),
                max_awg=max_awg,
                temperature=request.POST.get("temperature") or None,
                conduit=conduit_arg,
                ground=request.POST.get("ground"),
            )
        except Exception as exc:  # pragma: no cover - defensive
            context["error"] = str(exc)
        else:
            if result.get("awg") == "n/a":
                context["no_cable"] = True
            else:
                context["result"] = result
    context["templates"] = CalculatorTemplate.objects.filter(
        show_in_pages=True
    ).order_by("name")
    return render(request, "awg/calculator.html", context)


@csrf_exempt
@landing(_lazy("Energy Tariff Calculator"))
@login_required(login_url="pages:login")
def energy_tariff_calculator(request):
    """Estimate MXN costs for a given kWh consumption using CFE tariffs."""

    form_data = request.POST or request.GET
    form = {k: v for k, v in form_data.items() if v not in (None, "", "None")}

    base_qs = EnergyTariff.objects.filter(unit=EnergyTariff.Unit.KWH)
    years = sorted(base_qs.values_list("year", flat=True).distinct(), reverse=True)

    context: dict[str, object] = {
        "form": form,
        "years": years,
    }

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

    if request.method == "GET" and tariff_list and "tariff" not in form:
        form["tariff"] = str(tariff_list[0].pk)

    tariff_map = {str(t.pk): t for t in tariff_list}

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
            kwh_display = _format_decimal(kwh_value, "0.01")
            unit_price = _format_decimal(selected.price_mxn)
            unit_cost = _format_decimal(selected.cost_mxn)
            total_price = (kwh_display * unit_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_cost = (kwh_display * unit_cost).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            margin_total = (total_price - total_cost).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            context["result"] = {
                "tariff": selected,
                "kwh": kwh_display,
                "unit_price": unit_price,
                "unit_cost": unit_cost,
                "total_price": total_price,
                "total_cost": total_cost,
                "margin_total": margin_total,
            }
            form["kwh"] = str(kwh_display)

    return render(request, "awg/energy_tariff_calculator.html", context)
