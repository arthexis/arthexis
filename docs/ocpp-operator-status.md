# OCPP operator status handoff

Arthexis owns the authoritative, read-only OCPP operator status projection. Display hardware and rendering are consumers of that projection; they are not part of OCPP processing.

## Arthexis contract

Use `python manage.py ocpp_status --charger <identity> --json` for a machine-readable snapshot, or omit `--json` for stable line-oriented `key: value` output. The field order and JSON field set are defined by `DISPLAY_STATUS_FIELDS` in `apps.ocpp.services.display_status`.

The projection reports charger condition/state, pending-work age, processing rate and latency, recent errors/retries, recent authorization state, connection health, and snapshot time. Missing observations remain explicit as `null`/Python `None`; consumers must not infer activity from absent data.

## Consumer boundary

Gway and hardware-specific projects such as an e-paper renderer may ingest and render this output. They may refresh, cache, abbreviate, or paginate presentation data, but must not write OCPP state or participate in acknowledgement, ingestion, replay, authorization, durability, or backlog scheduling.

A display being absent, disconnected, slow, or broken must have no effect on charger processing. Arthexis therefore exposes this surface by querying its existing authoritative timeline/health snapshot only; the display contract has no callback into OCPP processing.

## GW004

GW004 can consume this status locally to show catch-up progress and live health while a historical backlog drains. Hardware layout, fonts, refresh cadence, and e-paper drivers remain outside Arthexis. This keeps the same Arthexis status contract usable by terminal output, control-node LCDs, e-paper satellites, and future operator surfaces.
