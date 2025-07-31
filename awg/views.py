from __future__ import annotations

from django.shortcuts import render
from website.utils import landing

from .models import CableSize, ConduitFill


def _format_size(size: str) -> str:
    """Return human readable label for an AWG size."""
    try:
        n = int(size)
    except (TypeError, ValueError):
        return size
    if n < 0:
        return f"{-n}/0"
    return size


def _fill_field(size: str) -> str:
    """Return the ConduitFill field name for an AWG size."""
    n = int(size)
    if n < 0:
        return "awg_" + "0" * (-n)
    return f"awg_{n}"


@landing("AWG Calculator")
def calculator(request):
    """Landing page providing basic AWG calculations."""
    awg_choices = [
        (s, _format_size(s))
        for s in sorted(
            CableSize.objects.values_list("awg_size", flat=True).distinct(),
            key=lambda x: int(x),
        )
    ]
    materials = [("cu", "Copper"), ("al", "Aluminum")]
    line_nums = sorted(
        CableSize.objects.values_list("line_num", flat=True).distinct()
    )

    awg_size = request.GET.get("awg_size")
    material = request.GET.get("material")
    line_num = request.GET.get("line_num")

    cable = None
    fills = []
    if awg_size and material and line_num:
        cable = CableSize.objects.filter(
            awg_size=awg_size, material=material, line_num=line_num
        ).first()
        if cable:
            field = _fill_field(awg_size)
            for f in ConduitFill.objects.exclude(**{field: None}):
                fills.append(
                    {
                        "trade_size": f.trade_size,
                        "conduit": f.conduit,
                        "value": getattr(f, field),
                    }
                )

    context = {
        "awg_choices": awg_choices,
        "materials": materials,
        "line_nums": line_nums,
        "cable": cable,
        "fills": fills,
    }
    return render(request, "awg/landing.html", context)
