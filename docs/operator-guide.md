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

Outbound charger commands are durable. If a process or live WebSocket is lost,
operators should use the persisted operation state rather than assuming a
command either succeeded or failed. See the
[OCPP recovery architecture](ocpp-recovery.md) for the meanings of PENDING,
DELIVERING, RECOVERY_REQUIRED, COMPLETED, and ERRORED and for automatic-retry
boundaries.

## Documentation navigation

Operator documentation uses ordinary Markdown links. Linking another repository Markdown file from this guide makes that document part of the public Markdown site; unlinked Markdown files remain unavailable over HTTP.

Return to the [Arthexis overview](../README.md).
