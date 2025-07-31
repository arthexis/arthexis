"""AWG calculator views and utilities."""

from __future__ import annotations

import math
from typing import Literal, Optional, Union

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from website.utils import landing

from .models import CableSize, ConduitFill


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
def calculator(
    request,
    *,
    meters=None,
    amps="40",
    volts="220",
    material="cu",
    max_lines="1",
    max_awg=None,
    phases="1",
    temperature="60",
    conduit=None,
    ground="1",
    neutral="0",
    **kwargs,
):
    """Display the AWG calculator form and results."""

    if request.method != "POST" or not request.POST.get("meters"):
        return HttpResponse(
            '''<link rel="stylesheet" href="/static/awg/cable_finder.css">
            <script src="/static/awg/calc_info.js"></script>
            <h1>Cable & Conduit Calculator</h1>
            <div class="calc-layout">
            <div class="form-wrapper">
            <form method="post" class="cable-form">
                <table class="form-table two-col">
                    <tr>
                        <td>
                            <label for="meters">Meters:</label>
                            <input id="meters" type="number" name="meters" required min="1" />
                        </td>
                        <td>
                            <label for="amps">Amps:</label>
                            <input id="amps" type="number" name="amps" value="40" />
                        </td>
                    </tr>
                    <tr>
                        <td>
                            <label for="volts">Volts:</label>
                            <input id="volts" type="number" name="volts" value="220" />
                        </td>
                        <td>
                            <label for="material">Material:</label>
                            <select id="material" name="material">
                                <option value="cu">Copper (cu)</option>
                                <option value="al">Aluminum (al)</option>
                            </select>
                        </td>
                    </tr>
                    <tr>
                        <td>
                            <label for="phases">Phases:</label>
                            <select id="phases" name="phases">
                                <option value="2">AC Two Phases (2)</option>
                                <option value="1">AC Single Phase (1)</option>
                                <option value="3">AC Three Phases (3)</option>
                            </select>
                        </td>
                        <td>
                            <label for="temperature">Temperature:</label>
                            <select id="temperature" name="temperature">
                                <option value="60" selected>60C (140F)</option>
                                <option value="75">75C (167F)</option>
                                <option value="90">90C (194F)</option>
                            </select>
                        </td>
                    </tr>
                    <tr>
                        <td>
                            <label for="conduit">Conduit:</label>
                            <select id="conduit" name="conduit">
                                <option value="emt" selected>EMT</option>
                                <option value="imc">IMC</option>
                                <option value="rmc">RMC</option>
                                <option value="fmc">FMC</option>
                                <option value="none">None</option>
                            </select>
                        </td>
                        <td>
                            <label for="max_awg">Max AWG:</label>
                            <input id="max_awg" type="text" name="max_awg" />
                        </td>
                    </tr>
                    <tr>
                        <td>
                            <label for="ground">Ground:</label>
                            <select id="ground" name="ground">
                                <option value="1" selected>1</option>
                                <option value="0">0</option>
                            </select>
                        </td>
                        <td>
                            <label for="max_lines">Max Lines:</label>
                            <select id="max_lines" name="max_lines">
                                <option value="1">1</option>
                                <option value="2">2</option>
                                <option value="3">3</option>
                                <option value="4">4</option>
                            </select>
                        </td>
                    </tr>
                    <tr>
                        <td>
                            <button type="submit" class="submit">Calculate</button>
                        </td>
                        <td></td>
                    </tr>
                </table>
            </form>
            </div>
            <div id="calc-info" class="calc-info">
                <button type="button" id="info-close" class="info-close hidden">[X]</button>
                <p>This tool helps you select cable sizes and conduit using standard AWG tables. Fill in your system details and press Calculate.</p>
                <h3>Glossary</h3>
                <ul class="glossary">
                    <li><strong>AWG</strong>: American Wire Gauge</li>
                    <li><strong>A</strong>: ampere (current)</li>
                    <li><strong>V</strong>: volt (voltage)</li>
                    <li><strong>m</strong>: meter (length)</li>
                    <li><strong>°C</strong>: degrees Celsius</li>
                    <li><strong>°F</strong>: degrees Fahrenheit</li>
                    <li><strong>cu</strong>: copper conductor</li>
                    <li><strong>al</strong>: aluminum conductor</li>
                </ul>
            </div>
            <button type="button" id="info-toggle" class="info-toggle">&#128278;</button>
            </div>
        ''')

    max_awg = request.POST.get("max_awg") or None
    conduit_field = request.POST.get("conduit")
    conduit_arg = None if conduit_field in (None, "", "none", "None") else conduit_field

    try:
        result = find_awg(
            meters=request.POST.get("meters"),
            amps=request.POST.get("amps", "40"),
            volts=request.POST.get("volts", "220"),
            material=request.POST.get("material", "cu"),
            max_lines=request.POST.get("max_lines", "1"),
            phases=request.POST.get("phases", "2"),
            max_awg=max_awg,
            temperature=request.POST.get("temperature"),
            conduit=conduit_arg,
            ground=request.POST.get("ground", "1"),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return HttpResponse(
            f"<p class='error'>Error: {exc}</p><p><a href='/awg/awg-calculator'>&#8592; Try again</a></p>"
        )

    if result.get("awg") == "n/a":
        return HttpResponse(
            """
            <h1>No Suitable Cable Found</h1>
            <p>No cable was found that meets the requirements within a 3% voltage drop.<br>
            Try adjusting the <b>cable size, amps, length, or material</b> and try again.</p>
            <p><a href="/awg/awg-calculator">&#8592; Calculate again</a></p>
        """
        )

    conduit_line = (
        f'<li><strong>Conduit:</strong> {result["conduit"].upper()} {result["pipe_inch"]}&quot;</li>'
        if result.get("pipe_inch")
        else ""
    )
    warning = (
        f"<p class='warning'>{result['warning']}</p>" if result.get("warning") else ""
    )

    return HttpResponse(
        f"""
        <div class='calc-result'>
        <h1>Calculator Results <img src='/static/awg/sponsor_logo.png' alt='Sponsor Logo' class='sponsor-logo'></h1>
        <ul class='result-list'>
            <li><strong>AWG Size:</strong> {result['awg']}</li>
            <li><strong>Lines:</strong> {result['lines']}</li>
            <li><strong>Total Cables:</strong> {result['cables']}</li>
            <li><strong>Total Length (m):</strong> {result['total_meters']}</li>
            <li><strong>Voltage Drop:</strong> {result['vdrop']:.2f} V ({result['vdperc']:.2f}%)</li>
            <li><strong>Voltage at End:</strong> {result['vend']:.2f} V</li>
            <li><strong>Temperature Rating:</strong> {result['temperature']}C</li>
            {conduit_line}
        </ul>
        {warning}
        <p>
        <em>Special thanks to the expert engineers at <strong>
        <a href="https://www.gelectriic.com" target="_blank">Gelectriic Solutions</a></strong> for their
        support in creating this calculator.</em>
        </p>
        <p><a href="/awg/awg-calculator">&#8592; Calculate again</a></p>
        </div>
    """
    )

