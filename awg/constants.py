"""Shared constants for the AWG application."""

from django.utils.translation import gettext_lazy as _lazy


#: Human friendly conduit labels reused across the calculator and admin forms.
CONDUIT_LABELS: dict[str, str] = {
    "emt": _lazy("EMT (Thin-wall)"),
    "imc": _lazy("IMC (Intermediate)"),
    "rmc": _lazy("RMC (Heavy-wall)"),
    "fmc": _lazy("FMC (Flex)"),
}


#: Conduit choices preserving the order expected by the calculator UI.
CONDUIT_CHOICES: list[tuple[str, str]] = [
    (key, CONDUIT_LABELS[key]) for key in ("emt", "imc", "rmc", "fmc")
]

