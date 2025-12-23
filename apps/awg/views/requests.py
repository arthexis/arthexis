"""AWG calculator views and utilities."""

from __future__ import annotations

import ipaddress
import math
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Iterable, Literal, Optional, Union

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from apps.sites.utils import get_original_referer, landing

from ..constants import CONDUIT_LABELS
from ..models import CableSize, CalculatorTemplate, ConduitFill, PowerLead


_ZAP_NUMERIC_FIELDS: tuple[str, ...] = (
    "meters",
    "amps",
    "volts",
    "max_lines",
    "phases",
    "temperature",
)

_AWG_PARAM_FIELDS: tuple[str, ...] = (
    "meters",
    "amps",
    "volts",
    "material",
    "max_awg",
    "max_lines",
    "phases",
    "temperature",
    "conduit",
    "ground",
)


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
    try:
        return int(value), ""
    except (TypeError, ValueError):
        raise ValueError(_("Ground must be 0, 1, or [1]."))


def _format_ground_output(amount: int, label: str) -> str:
    """Return a formatted ground string including any special label."""

    return f"{amount} ({label})" if label else str(amount)


def _normalize_special_value(value: Optional[Union[str, int]]) -> str:
    """Return ``value`` normalized for keyword comparisons."""

    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _contains_zap(values: Iterable[Optional[Union[str, int]]]) -> bool:
    """Return ``True`` when any ``values`` contain the zap keyword."""

    return any(_normalize_special_value(value) == "zap" for value in values)


def _clean_awg_params(data: MutableMapping[str, str]) -> dict[str, str]:
    """Return calculator parameters stripped of blanks and unsupported fields."""

    allowed_fields = set(_AWG_PARAM_FIELDS) | {"template"}
    return {
        key: value
        for key, value in data.items()
        if key in allowed_fields and value not in (None, "", "None")
    }


def _load_awg_data(
    *,
    material: str,
    max_lines: int,
    force_awg: Optional[Union[int, str]] = None,
    limit_awg: Optional[Union[int, str]] = None,
):
    """Return ampacity data for each AWG/line combination respecting limits."""

    target_force = int(AWG(force_awg)) if force_awg is not None else None
    target_limit = int(AWG(limit_awg)) if limit_awg is not None else None

    qs = CableSize.objects.filter(material=material, line_num__lte=max_lines)
    awg_data: dict[int, dict[int, dict[str, float]]] = {}
    for row in qs.values_list(
        "awg_size", "line_num", "k_ohm_km", "amps_60c", "amps_75c", "amps_90c"
    ):
        awg_size, line_num, k_ohm, a60, a75, a90 = row
        awg_int = int(AWG(awg_size))
        if target_force is not None and awg_int != target_force:
            continue
        if target_limit is not None and awg_int > target_limit:
            continue
        awg_data.setdefault(awg_int, {})[int(line_num)] = {
            "k": k_ohm,
            "a60": a60,
            "a75": a75,
            "a90": a90,
        }
    return awg_data


def _prepare_sizes(
    awg_data: dict[int, dict[int, dict[str, float]]],
    *,
    force_awg: Optional[Union[int, str]] = None,
    limit_awg: Optional[Union[int, str]] = None,
):
    """Return the ordered AWG sizes that should be evaluated."""

    if force_awg is not None:
        forced = int(AWG(force_awg))
        return [forced] if forced in awg_data else []
    if limit_awg is None:
        return sorted(awg_data.keys(), reverse=True)
    limit_value = int(AWG(limit_awg))
    return sorted([size for size in awg_data if size <= limit_value], reverse=True)


def _line_capacities(base: dict[str, float], info: Optional[dict[str, float]], lines: int):
    """Return the ampacity values for the requested number of lines."""

    if info:
        return info["a60"], info["a75"], info["a90"]
    return base["a60"] * lines, base["a75"] * lines, base["a90"] * lines


def _is_ampacity_allowed(
    *,
    amps: int,
    temperature: Optional[int],
    a60: float,
    a75: float,
    a90: float,
):
    """Return ``True`` when the ampacity meets the requested load."""

    if temperature is None:
        return (amps > 100 and a75 >= amps) or (amps <= 100 and a60 >= amps)
    return {60: a60, 75: a75, 90: a90}.get(temperature, 0) >= amps


def _build_result(
    *,
    awg_size: int,
    lines: int,
    vdrop: float,
    perc: float,
    meters: int,
    amps: int,
    volts: int,
    temperature: Optional[int],
    phases: int,
    ground_count: int,
    ground_label: str,
):
    """Assemble the response payload for a candidate AWG size."""

    ground_total = lines * ground_count
    return {
        "awg": str(AWG(awg_size)),
        "awg_display": _display_awg(awg_size),
        "meters": meters,
        "amps": amps,
        "volts": volts,
        "temperature": temperature if temperature is not None else (60 if amps <= 100 else 75),
        "lines": lines,
        "vdrop": vdrop,
        "vend": volts - vdrop,
        "vdperc": perc * 100,
        "cables": f"{lines * phases}+{_format_ground_output(ground_total, ground_label)}",
        "total_meters": f"{lines * phases * meters}+{_format_ground_output(meters * ground_total, ground_label)}",
    }


def _attach_conduit(
    result: dict[str, object],
    *,
    conduit: Optional[Union[str, bool]],
    phases: int,
    ground_count: int,
):
    """Add conduit information to ``result`` when requested."""

    if not conduit or result.get("awg") == "n/a":
        return

    conduit_value = "emt" if conduit is True else conduit
    cables = result["lines"] * (phases + ground_count)
    fill = find_conduit(AWG(result["awg"]), cables, conduit=conduit_value)
    result["conduit"] = conduit_value
    result["conduit_label"] = CONDUIT_LABELS.get(
        str(conduit_value).lower(), str(conduit_value).upper()
    )
    result["pipe_inch"] = fill["size_inch"]


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


@dataclass(frozen=True)
class _AwgParameters:
    """Container for validated AWG calculator inputs."""

    amps: int
    meters: int
    volts: int
    material: str
    max_lines: int
    phases: int
    temperature: Optional[int]
    max_awg: Optional[AWG]
    conduit: Optional[Union[str, bool]]
    ground_label: str
    ground_options: tuple[int, ...]


def _coerce_int(value, label, *, required=True, default=None) -> int:
    """Return ``value`` as an ``int`` or raise a translated ``ValueError``."""

    if value in (None, "", "None"):
        if not required:
            return default
        raise ValueError(_("%(field)s is required.") % {"field": label})
    try:
        return int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(_("%(field)s must be a whole number.") % {"field": label}) from exc


def _parse_awg_parameters(
    *,
    meters: Union[int, str, None],
    amps: Union[int, str],
    volts: Union[int, str],
    material: Literal["cu", "al", "?"] = "cu",
    max_awg: Optional[Union[int, str]] = None,
    max_lines: Union[int, str] = "1",
    phases: Union[str, int] = "2",
    temperature: Union[int, str, None] = None,
    conduit: Optional[Union[str, bool]] = None,
    ground: Union[int, str] = "1",
) -> _AwgParameters:
    """Normalise and validate user-provided inputs for ``find_awg``."""

    amps_int = _coerce_int(amps, _lazy("Amps"))
    meters_int = _coerce_int(meters, _lazy("Meters"))
    volts_int = _coerce_int(volts, _lazy("Volts"))
    max_lines_int = _coerce_int(
        max_lines, _lazy("Max Lines"), required=False, default=1
    )

    max_awg_value: Optional[AWG]
    if max_awg in (None, ""):
        max_awg_value = None
    else:
        try:
            max_awg_value = AWG(max_awg)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(_("Max AWG must be a valid gauge value.")) from exc

    phases_int = _coerce_int(phases, _lazy("Phases"))
    if temperature in (None, "", "auto"):
        temperature_int = None
    else:
        temperature_int = _coerce_int(temperature, _lazy("Temperature"))

    ground_value, ground_label = _parse_ground(ground)
    ground_options = (1, 0) if ground_label == "[1]" else (ground_value,)

    params = _AwgParameters(
        amps=amps_int,
        meters=meters_int,
        volts=volts_int,
        material=material,
        max_lines=max_lines_int,
        phases=phases_int,
        temperature=temperature_int,
        max_awg=max_awg_value,
        conduit=conduit,
        ground_label=ground_label,
        ground_options=ground_options,
    )

    _validate_awg_parameters(params)
    return params


def _validate_awg_parameters(params: _AwgParameters) -> None:
    """Ensure ``params`` satisfies business constraints for the calculator."""

    assert params.amps >= 10, _(
        "Minimum load for this calculator is 15 Amps.  Yours: amps=%(amps)s."
    ) % {"amps": params.amps}
    assert (
        (params.amps <= 546) if params.material == "cu" else (params.amps <= 430)
    ), _(
        "Max. load allowed is 546 A (cu) or 430 A (al). Yours: amps=%(amps)s material=%(material)s"
    ) % {"amps": params.amps, "material": params.material}
    assert params.meters >= 1, _("Consider at least 1 meter of cable.")
    assert 110 <= params.volts <= 460, _(
        "Volt range supported must be between 110-460. Yours: volts=%(volts)s"
    ) % {"volts": params.volts}
    assert params.material in ("cu", "al"), _(
        "Material must be 'cu' (copper) or 'al' (aluminum)."
    )
    assert params.phases in (1, 2, 3), _(
        "AC phases 1, 2 or 3 to calculate for. DC not supported."
    )
    if params.temperature is not None:
        assert params.temperature in (60, 75, 90), _(
            "Temperature must be 60, 75 or 90"
        )


def _base_vdrop(params: _AwgParameters) -> float:
    """Return the voltage drop baseline used in the AWG iteration."""

    multiplier = math.sqrt(3) if params.phases in (2, 3) else 2
    return multiplier * params.meters * params.amps / 1000


def _iter_awg_candidates(
    *,
    params: _AwgParameters,
    awg_data: dict[int, dict[int, dict[str, float]]],
    ground_count: int,
    base_vdrop: float,
    force_awg: Optional[Union[int, str]],
    limit_awg: Optional[Union[int, str]],
):
    """Yield candidate AWG results along with their evaluation metadata."""

    for awg_size in _prepare_sizes(
        awg_data, force_awg=force_awg, limit_awg=limit_awg
    ):
        base = awg_data[awg_size][1]
        for lines in range(1, params.max_lines + 1):
            info = awg_data[awg_size].get(lines)
            a60, a75, a90 = _line_capacities(base, info, lines)
            allowed = _is_ampacity_allowed(
                amps=params.amps,
                temperature=params.temperature,
                a60=a60,
                a75=a75,
                a90=a90,
            )
            if not allowed and force_awg is None:
                continue

            vdrop = base_vdrop * base["k"] / lines
            perc = vdrop / params.volts
            result = _build_result(
                awg_size=awg_size,
                lines=lines,
                vdrop=vdrop,
                perc=perc,
                meters=params.meters,
                amps=params.amps,
                volts=params.volts,
                temperature=params.temperature,
                phases=params.phases,
                ground_count=ground_count,
                ground_label=params.ground_label,
            )
            yield allowed, perc, result


def _calculate_awg_for_ground(
    params: _AwgParameters,
    ground_count: int,
    *,
    force_awg: Optional[Union[int, str]] = None,
    limit_awg: Optional[Union[int, str]] = None,
) -> dict[str, object]:
    """Return the best AWG match for ``ground_count`` wires."""

    awg_data = _load_awg_data(
        material=params.material,
        max_lines=params.max_lines,
        force_awg=force_awg,
        limit_awg=limit_awg,
    )
    base_vdrop = _base_vdrop(params)

    best: Optional[dict[str, object]] = None
    best_perc = 1e9

    for allowed, perc, result in _iter_awg_candidates(
        params=params,
        awg_data=awg_data,
        ground_count=ground_count,
        base_vdrop=base_vdrop,
        force_awg=force_awg,
        limit_awg=limit_awg,
    ):
        if allowed and perc <= 0.03:
            _attach_conduit(
                result,
                conduit=params.conduit,
                phases=params.phases,
                ground_count=ground_count,
            )
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
        _attach_conduit(
            best,
            conduit=params.conduit,
            phases=params.phases,
            ground_count=ground_count,
        )
        return best

    return {"awg": "n/a", "awg_display": "n/a"}


def _solve_for_ground(params: _AwgParameters, ground_count: int) -> dict[str, object]:
    """Solve the AWG calculation for a specific ground configuration."""

    baseline = _calculate_awg_for_ground(params, ground_count)
    if params.max_awg is None:
        return baseline

    if baseline.get("awg") == "n/a":
        return _calculate_awg_for_ground(
            params, ground_count, limit_awg=params.max_awg
        )

    if int(AWG(baseline["awg"])) < int(params.max_awg):
        return _calculate_awg_for_ground(
            params, ground_count, force_awg=params.max_awg
        )
    return _calculate_awg_for_ground(
        params, ground_count, limit_awg=params.max_awg
    )


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
    """Calculate the cable size required for given parameters."""

    params = _parse_awg_parameters(
        meters=meters,
        amps=amps,
        volts=volts,
        material=material,
        max_awg=max_awg,
        max_lines=max_lines,
        phases=phases,
        temperature=temperature,
        conduit=conduit,
        ground=ground,
    )

    results = [
        (count, _solve_for_ground(params, count)) for count in params.ground_options
    ]
    if len(results) == 1:
        return results[0][1]

    vd_results = [item for item in results if "vdperc" in item[1]]
    if vd_results:
        worst = max(vd_results, key=lambda item: item[1]["vdperc"])
        return worst[1]

    return results[0][1]


def _template_defaults(template: CalculatorTemplate) -> dict[str, object]:
    """Return parameters derived from the provided calculator ``template``."""

    return {
        field: getattr(template, field)
        for field in _AWG_PARAM_FIELDS
        if getattr(template, field) not in (None, "")
    }


def awg_calculate(request):
    """Calculate cable size using query parameters or a stored template."""

    params = _clean_awg_params(request.POST or request.GET)
    template_value = params.pop("template", None)

    template_params: dict[str, object] = {}
    if template_value:
        try:
            lookup = {"pk": int(template_value)}
        except (TypeError, ValueError):
            lookup = {"name__iexact": template_value}

        template = CalculatorTemplate.objects.filter(**lookup).first()
        if template is None:
            return JsonResponse(
                {"error": _("Calculator template not found.")}, status=404
            )
        template_params = _template_defaults(template)

    merged_params = {**template_params, **params}
    if "meters" not in merged_params:
        return JsonResponse(
            {"error": _("Meters is required for AWG calculation.")}, status=400
        )

    try:
        result = find_awg(**merged_params)
    except (AssertionError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(result)


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
        zap_values = (request.POST.get(field) for field in _ZAP_NUMERIC_FIELDS)
        if _contains_zap(zap_values):
            _flag_zapped_display(request)
            PowerLead.objects.create(
                user=request.user if request.user.is_authenticated else None,
                values=lead_values,
                path=request.path,
                referer=get_original_referer(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                ip_address=_extract_client_ip(),
                malformed=False,
            )
            return redirect("awg:zapped")
        max_awg = request.POST.get("max_awg") or None
        conduit_field = request.POST.get("conduit")
        conduit_arg = None if conduit_field in (None, "") else conduit_field
        malformed = False
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
            malformed = True
        else:
            if result.get("awg") == "n/a":
                context["no_cable"] = True
            else:
                context["result"] = result
        PowerLead.objects.create(
            user=request.user if request.user.is_authenticated else None,
            values=lead_values,
            path=request.path,
            referer=get_original_referer(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            ip_address=_extract_client_ip(),
            malformed=malformed,
        )
    context["templates"] = CalculatorTemplate.objects.filter(
        show_in_pages=True
    ).order_by("name")
    return render(request, "awg/calculator.html", context)


ZAPPED_SESSION_KEY = "awg:zapped_allowed"


def _allow_zapped_display(request: "HttpRequest") -> bool:
    """Return ``True`` when the zap easter egg may be displayed."""

    session = getattr(request, "session", None)
    if not hasattr(session, "get"):
        return False

    allowed = session.get(ZAPPED_SESSION_KEY, False)
    if allowed:
        session[ZAPPED_SESSION_KEY] = False
    return bool(allowed)


def _flag_zapped_display(request: "HttpRequest") -> None:
    """Mark the zap easter egg as displayable for the current session."""

    session = getattr(request, "session", None)
    if hasattr(session, "__setitem__"):
        session[ZAPPED_SESSION_KEY] = True


def zapped_result(request: "HttpRequest"):
    """Display the playful zap easter egg response."""

    if not _allow_zapped_display(request):
        return redirect("awg:calculator")

    context = {
        "zap_message": _lazy("Ouch! I've been zapped!! Now it's my turn..."),
    }
    return render(request, "awg/zapped.html", context)
