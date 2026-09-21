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

OCPP application routes are served beneath `/ocpp/`. Device-specific commissioning and recovery procedures can be added here as they are migrated into the 2.0 operator documentation set.

## Documentation navigation

Operator documentation uses ordinary Markdown links. Linking another repository Markdown file from this guide makes that document part of the public Markdown site; unlinked Markdown files remain unavailable over HTTP.

Return to the [Arthexis overview](../README.md).
