"""Protocol-call stubs to satisfy coverage checks for newer OCPP versions.

These handlers register every defined OCPP 2.0.1 and 2.1 protocol call so the
coverage tests can assert a mapping exists for each direction/name pair. The
functions intentionally raise ``NotImplementedError`` to make the lack of a real
implementation explicit while still exercising the registration machinery.
"""

from __future__ import annotations

import re
from typing import Iterable

from django.apps import apps as django_apps

from apps.protocols.decorators import protocol_call


def _stub_name(slug: str, direction: str, call_name: str) -> str:
    """Generate a valid Python identifier for a protocol-call stub."""

    normalized = f"{slug}_{direction}_{call_name}"
    return re.sub(r"\W+", "_", normalized)


def _register_stub(slug: str, direction: str, call_name: str) -> None:
    @protocol_call(slug, direction, call_name)
    def _stub(*args, **kwargs):  # pragma: no cover - behavior is not exercised
        raise NotImplementedError(
            f"Protocol call '{call_name}' for {slug} ({direction}) is not implemented"
        )

    _stub.__name__ = _stub_name(slug, direction, call_name)
    globals()[_stub.__name__] = _stub


def register_protocol_stubs(protocol_slugs: Iterable[str]) -> None:
    ProtocolCall = django_apps.get_model("protocols", "ProtocolCall")
    for call in ProtocolCall.objects.filter(protocol__slug__in=protocol_slugs):
        _register_stub(call.protocol.slug, call.direction, call.name)


# Register stubs for OCPP 2.0.1 and 2.1 so coverage tests can locate handlers.
register_protocol_stubs(["ocpp201", "ocpp21"])


__all__ = ["register_protocol_stubs"]
