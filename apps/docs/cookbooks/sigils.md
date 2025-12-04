# Arthexis Sigils Cookbook

Sigils are bracketed tokens such as `[ENV.SMTP_PASSWORD]` that Arthexis expands at runtime. They make it possible to reference configuration secrets, system metadata, or records stored in other apps without duplicating values across the project. This cookbook explains how the system resolves sigils, how to inspect the available prefixes, and how to introduce new ones safely.

- [1. When to use sigils](#1-when-to-use-sigils)
- [2. Syntax reference](#2-syntax-reference)
- [3. Built-in prefixes](#3-built-in-prefixes)
- [4. Managing Sigil Roots](#4-managing-sigil-roots)
- [5. Troubleshooting and observability](#5-troubleshooting-and-observability)

---

## 1. When to use sigils

Use sigils whenever an integration needs values that already live elsewhere in the platform. Common examples include:

- Expanding environment-specific credentials (`[ENV.SMTP_PASSWORD]`, `[ENV.DATABASE_URL]`).
- Reusing Django settings in templates or background tasks (`[CONF.DEFAULT_FROM_EMAIL]`).
- Pulling metadata about the running node (`[SYS.ROLE]`, `[SYS.VERSION]`).
- Looking up Django model fields by natural keys without additional queries (for example `[USER=username.email]`).

Because sigils resolve just before the data is used, they keep configurations DRY and ensure updates propagate everywhere without editing multiple files.

## 2. Syntax reference

Sigils always start with `[` and end with `]`. The following patterns are supported:

- `[PREFIX.KEY]` &mdash; returns a field or attribute. Hyphens and casing are normalized automatically so `[env.smtp-password]` and `[ENV.SMTP_PASSWORD]` behave the same.
- `[PREFIX=IDENTIFIER.FIELD]` &mdash; selects a specific record by primary key or any unique field declared as a natural key.
- `[PREFIX:FIELD=VALUE.ATTRIBUTE]` &mdash; filters by a custom field instead of the primary key.
- `[PREFIX.FIELD=[OTHER.SIGIL]]` &mdash; nests sigils so the value after `=` resolves before the outer token.
- `[PREFIX]` &mdash; for entity prefixes, returns the serialized object in JSON; for configuration prefixes, resolves to an empty string when the key is missing.

## 3. Built-in prefixes

Arthexis ships with several prefixes out of the box:

- `ENV` reads environment variables.
- `CONF` reads Django settings.
- `SYS` exposes computed system information such as build metadata, the active node role, and runtime versions.

Additional prefixes become available as soon as they are defined as Sigil Roots. Each root maps a short code (for example `ROLE`, `ODOO`, or `USER`) to a Django model and enumerates which fields can be resolved.

## 4. Managing Sigil Roots

The **Sigil Builder** interface lives in the Django admin under **Admin → Sigil Builder** (`/admin/sigil-builder/`). From there you can:

1. Review every registered prefix, the associated Django model, and the fields exposed through sigils.
2. Use the built-in test console to preview how a token will resolve before adopting it in configuration files.
3. Add new roots or edit existing ones when a new integration needs additional prefixes.

When adding a root:

- Choose a short, descriptive code so tokens remain readable.
- Prefer natural keys over numeric primary keys so tokens stay stable across environments.
- Document the intended usage in the `notes` field so future operators understand the context.

Changes take effect immediately—no service restart is required—so review tokens carefully in the test console before saving.

## 5. Troubleshooting and observability

- Unknown prefixes remain in place (for example `[UNKNOWN.VALUE]`) and are logged so you can spot typos quickly.
- Failed lookups for entity prefixes raise descriptive errors in the logs; configuration prefixes resolve to an empty string when the key is missing.
- Use `python manage.py sigil_resolve "[PREFIX.KEY]"` from a shell (when available) or the Sigil Builder test console to verify a token outside of production workflows.
- Keep secrets in environment variables or encrypted configuration stores and reference them through `ENV` rather than duplicating credentials in plain text.

By centralizing dynamic configuration behind sigils, administrators can keep installations consistent, minimise drift between environments, and grant integrators self-service access to the data they need without exposing sensitive details directly.
