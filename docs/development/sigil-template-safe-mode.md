# Sigil template safe mode

Arthexis now supports user-safe sigil rendering directly in Django templates through `apps.sigils.templatetags.sigils`.

## Template APIs

```django
{% load sigils %}
{% sigil_expr "CP:hostname:SIM-CP-1|GET:role" %}
{{ "role=[CP:hostname:SIM-CP-1|GET:role]"|sigils }}
```

`sigil_expr` resolves a single expression and auto-wraps bare expressions with `[]`.
`sigils` resolves existing placeholder-rich text.

Both APIs enforce user-safe policy:

- only roots with `is_user_safe=True` are allowed,
- only approved pipeline actions are allowed,
- request/current-object context binding uses sigil context thread-local mechanisms.

## Admin toggle: unresolved output behavior

Operators can tune safe-mode fallback in **Admin → Sigil Render Policy**.

`unresolved_behavior` options:

- `placeholder`: keep unresolved/disallowed tokens visible (for diagnostics),
- `empty`: collapse unresolved/disallowed output to an empty string.

This lets operators tune end-user output handling without code edits.
