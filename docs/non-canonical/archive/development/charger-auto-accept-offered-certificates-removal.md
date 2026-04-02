# Charger auto-accept offered certificates field removal

> **Non-canonical reference:** This document is retained for internal or historical context and is not part of the canonical Arthexis documentation set.

## Backward compatibility note

`Charger.auto_accept_offered_certificates` has been removed from the Django model and database schema.

This flag had already been a runtime no-op before removal: certificate-status handling rejected unknown certificates regardless of the field value.

## Upgrade impact

- Existing deployments can apply migrations normally; the obsolete column is dropped.
- Runtime behavior remains unchanged because the flag was not consulted by active certificate-status logic.
- Admin no longer exposes the removed field.
