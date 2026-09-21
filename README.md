# Arthexis

Arthexis is a field-safe EV charging operations and OCPP service platform.

The 2.0 rebuild keeps the runtime intentionally small: Django provides the operational service surface, Gway handles deployment and host orchestration, and operator-facing guidance can live as version-controlled Markdown.

## Operator documentation

Start with the [Operator Guide](docs/operator-guide.md).

Only Markdown documents linked from this README, directly or through another exposed Markdown document, are published by the running site. Repository documentation that is not part of that link graph remains private to the source tree.

## Service endpoints

- `/health/` — lightweight HTTP liveness check.
- `/ocpp/` — OCPP application routes.
- Administrative access exists separately and is intentionally not advertised in the public navigation.

## Development

Source code and development documentation are maintained in the [Arthexis repository](https://github.com/arthexis/arthexis).
