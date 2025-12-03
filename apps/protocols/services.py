from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from django.db import models, transaction

from apps.protocols.models import Protocol, ProtocolCall

SPEC_DIR = Path(__file__).resolve().parent / "spec"


class ProtocolSpecError(RuntimeError):
    pass


def spec_path(slug: str) -> Path:
    return SPEC_DIR / f"{slug}.json"


def load_protocol_spec_from_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "calls" in data:
        calls = data.get("calls", {})
    else:
        # Legacy schema containing only call directions
        calls = {
            "cp_to_csms": data.get("cp_to_csms", []),
            "csms_to_cp": data.get("csms_to_cp", []),
        }
        data.setdefault("slug", path.stem)
        data.setdefault("name", path.stem.upper())
        data.setdefault("version", path.stem.replace("ocpp", "").replace("_", "."))
    normalized = {
        "slug": data.get("slug", path.stem),
        "name": data.get("name", path.stem.upper()),
        "version": str(data.get("version", "")).strip() or path.stem,
        "description": data.get("description", ""),
        "calls": {
            "cp_to_csms": list(dict.fromkeys(calls.get("cp_to_csms", []))),
            "csms_to_cp": list(dict.fromkeys(calls.get("csms_to_cp", []))),
        },
    }
    if not normalized["slug"]:
        raise ProtocolSpecError("Protocol slug is required")
    return normalized


@transaction.atomic
def sync_protocol_from_spec(spec: dict) -> Protocol:
    protocol, _created = Protocol.objects.update_or_create(
        slug=spec["slug"],
        defaults={
            "name": spec.get("name") or spec["slug"],
            "version": spec.get("version", ""),
            "description": spec.get("description", ""),
        },
    )
    seen: set[tuple[str, str]] = set()
    for direction, calls in spec.get("calls", {}).items():
        for call_name in calls:
            ProtocolCall.objects.update_or_create(
                protocol=protocol,
                name=call_name,
                direction=direction,
            )
            seen.add((direction, call_name))
    preserve_q: models.Q | None = None
    for direction, name in seen:
        clause = models.Q(direction=direction, name=name)
        preserve_q = clause if preserve_q is None else preserve_q | clause
    if preserve_q is None:
        ProtocolCall.objects.filter(protocol=protocol).delete()
    else:
        ProtocolCall.objects.filter(protocol=protocol).exclude(preserve_q).delete()
    return protocol


def dump_protocol_to_spec(protocol: Protocol) -> dict:
    calls = protocol.calls.all()
    return {
        "slug": protocol.slug,
        "name": protocol.name,
        "version": protocol.version,
        "description": protocol.description,
        "calls": {
            ProtocolCall.CP_TO_CSMS: sorted(
                call.name for call in calls if call.direction == ProtocolCall.CP_TO_CSMS
            ),
            ProtocolCall.CSMS_TO_CP: sorted(
                call.name for call in calls if call.direction == ProtocolCall.CSMS_TO_CP
            ),
        },
    }


def export_protocol_spec(protocol_slug: str, output: Path) -> Path:
    protocol = Protocol.objects.get(slug=protocol_slug)
    spec = dump_protocol_to_spec(protocol)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def import_protocol_spec(protocol_slug: str, source: Path | None = None) -> Protocol:
    path = source or spec_path(protocol_slug)
    spec = load_protocol_spec_from_file(path)
    if spec.get("slug") != protocol_slug:
        raise ProtocolSpecError(
            f"Spec slug {spec.get('slug')} does not match requested {protocol_slug}"
        )
    return sync_protocol_from_spec(spec)


__all__ = [
    "ProtocolSpecError",
    "load_protocol_spec_from_file",
    "sync_protocol_from_spec",
    "dump_protocol_to_spec",
    "export_protocol_spec",
    "import_protocol_spec",
    "spec_path",
]
