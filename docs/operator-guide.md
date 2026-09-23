# Operator Guide

This guide is the starting point for operator-facing procedures in Arthexis.

## Availability

Use the public health endpoint to confirm that the deployed application is responding:

```text
https://arthexis.com/health/
```

A successful response is:

```json
{"status": "ok"}
```

## OCPP

OCPP application routes are served beneath `/ocpp/`.

Outbound charger commands are durable. Protocol ambiguity is retained for audit,
but field recovery is charger/session-centric rather than operation-centric.

Inspect the current derived charger state with:

```text
python manage.py ocpp_recovery --charger CP-0042
```

The recovery view explains why Arthexis selected the current state, including
connection presence freshness, current or unresolved session evidence, and what
new evidence or operator verification is required before the state can change.

If the physical charger is known to be idle but Arthexis is still stuck in a
charging or unresolved state, clear the stale current interpretation with:

```text
python manage.py ocpp_recovery \
    --charger CP-0042 \
    --clear-stale-state \
    --reason "verified idle at charger"
```

This does not fabricate a StopTransaction, invent a stop timestamp, rewrite the
raw connector evidence, or resend ambiguous protocol commands. The affected
session remains retained as operator-cleared forensic history. Older buffered
meter traffic cannot reactivate it; genuinely newer charger evidence may.

See the [OCPP recovery architecture](ocpp-recovery.md) for durable protocol
operation lifecycle and automatic-retry boundaries.

## Documentation navigation

Operator documentation uses ordinary Markdown links. Linking another repository Markdown file from this guide makes that document part of the public Markdown site; unlinked Markdown files remain unavailable over HTTP.

Return to the [Arthexis overview](../README.md).
