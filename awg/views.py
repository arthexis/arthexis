"""AWG calculator views and utilities."""

from __future__ import annotations

import math
from typing import Literal, Optional, Union

from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from website.utils import landing

from .models import CableSize, ConduitFill, CalculatorTemplate


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


def find_conduit(awg: Union[str, int], cables: int, *, conduit: str = "emt"):
    """Return the conduit trade size capable of holding *cables* wires."""

    awg = AWG(awg)
    field = _fill_field(awg)
    qs = (
        ConduitFill.objects
        .filter(conduit__iexact=conduit)
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
    ground = int(ground)

    assert amps >= 10, (
        f"Minimum load for this calculator is 15 Amps.  Yours: {amps=}."
    )
    assert (amps <= 546) if material == "cu" else (amps <= 430), (
        f"Max. load allowed is 546 A (cu) or 430 A (al). Yours: {amps=} {material=}"
    )
    assert meters >= 1, "Consider at least 1 meter of cable."
    assert 110 <= volts <= 460, f"Volt range supported must be between 110-460. Yours: {volts=}"
    assert material in ("cu", "al"), "Material must be 'cu' (copper) or 'al' (aluminum)."
    assert phases in (1, 2, 3), "AC phases 1, 2 or 3 to calculate for. DC not supported."
    if temperature is not None:
        assert temperature in (60, 75, 90), "Temperature must be 60, 75 or 90"

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
            if limit_awg is not None and awg_int < int(AWG(limit_awg)):
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
            sizes = sorted([s for s in awg_data.keys() if s >= int(AWG(limit_awg))])

        for awg_size in sizes:
            base = awg_data[awg_size][1]
            for n in range(1, max_lines + 1):
                info = awg_data[awg_size].get(n)
                a60 = (info or base)["a60"] if info else base["a60"] * n
                a75 = (info or base)["a75"] if info else base["a75"] * n
                a90 = (info or base)["a90"] if info else base["a90"] * n
                if temperature is None:
                    allowed = ((a75 >= amps and amps > 100) or (a60 >= amps and amps <= 100))
                else:
                    tmap = {60: a60, 75: a75, 90: a90}
                    allowed = tmap.get(temperature, 0) >= amps
                if not allowed and force_awg is None:
                    continue

                vdrop = base_vdrop * base["k"] / n
                perc = vdrop / volts
                result = {
                    "awg": str(AWG(awg_size)),
                    "meters": meters,
                    "amps": amps,
                    "volts": volts,
                    "temperature": (
                        temperature if temperature is not None else (60 if amps <= 100 else 75)
                    ),
                    "lines": n,
                    "vdrop": vdrop,
                    "vend": volts - vdrop,
                    "vdperc": perc * 100,
                    "cables": f"{n * phases}+{n * ground}",
                    "total_meters": f"{n * phases * meters}+{meters * n * ground}",
                }
                if force_awg is None:
                    if allowed and perc <= 0.03:
                        if conduit:
                            c = "emt" if conduit is True else conduit
                            fill = find_conduit(AWG(awg_size), n * (phases + ground), conduit=c)
                            result["conduit"] = c
                            result["pipe_inch"] = fill["size_inch"]
                        return result
                    if perc < best_perc:
                        best = result
                        best_perc = perc
                else:
                    if allowed and perc <= 0.03:
                        if conduit:
                            c = "emt" if conduit is True else conduit
                            fill = find_conduit(AWG(awg_size), n * (phases + ground), conduit=c)
                            result["conduit"] = c
                            result["pipe_inch"] = fill["size_inch"]
                        return result
                    if perc < best_perc:
                        best = result
                        best_perc = perc

        if best and (force_awg is not None or limit_awg is not None):
            if force_awg is not None:
                best["warning"] = "Voltage drop may exceed 3% with chosen parameters"
            else:
                best["warning"] = "Voltage drop exceeds 3% with given max_awg"
            if conduit:
                c = "emt" if conduit is True else conduit
                fill = find_conduit(AWG(best["awg"]), best["lines"] * (phases + ground), conduit=c)
                best["conduit"] = c
                best["pipe_inch"] = fill["size_inch"]
            return best

        return {"awg": "n/a"}

    baseline = _calc()
    if max_awg is None:
        return baseline

    if baseline.get("awg") == "n/a":
        return _calc(limit_awg=max_awg)

    if int(AWG(baseline["awg"])) < int(max_awg):
        return _calc(force_awg=max_awg)
    return _calc(limit_awg=max_awg)


@csrf_exempt
@landing("AWG Calculator")
def calculator(request):
    """Display the AWG calculator form and results using a template."""
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
    context["templates"] = (
        CalculatorTemplate.objects.filter(show_in_website=True).order_by("name")
    )
    return render(request, "awg/calculator.html", context)
