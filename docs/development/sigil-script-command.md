# `solve` / `resolve` sigil management commands

Use Arthexis sigil resolver semantics from the CLI to evaluate one expression or a deterministic `.artx` script.

## Command synopsis

```bash
.venv/bin/python manage.py solve --expr "[CP:hostname=SIM-CP-1.public_endpoint]"
.venv/bin/python manage.py resolve --file scripts/ocpp_flow.artx
```

Inputs:

- `--expr`: run one inline expression (wrapped as an `EMIT` instruction).
- `--file`: run a `.artx` script.
- `--context`: optional policy context (`admin`, `user`, `request`), default `admin`.
- `--output`: output format (`text`, `json`), default `text`.
- `--cache` / `--no-cache`: override cache behavior for the current run.

Defaults:

- `solve`: cache disabled by default.
- `resolve`: synonym of `solve` with cache enabled by default.

`run_sigil_script` remains as a backward-compatible alias of `solve`.

## `.artx` script semantics

Statements are deterministic and executed top-to-bottom:

- `LET NAME = EXPR`
- `EMIT EXPR`

Identifiers are normalized to uppercase. Statement actions are also uppercase by default.

Use `$NAME` inside later expressions to interpolate prior `LET` values.

Example:

```text
LET CP_HOST = [CP:hostname=SIM-CP-1.public_endpoint]
LET NODE_LABEL = [NODE:hostname=SIM-NODE-1.hostname]
EMIT OCPP route CP:$CP_HOST NODE:$NODE_LABEL
EMIT Task audit [TASK:status=queued.id]
```

## OCPP-oriented examples

Evaluate one expression for a charge point root:

```bash
.venv/bin/python manage.py solve \
  --expr "[CP:hostname=SIM-CP-1.public_endpoint]" \
  --context admin
```

Run a script for request-safe inspection output:

```bash
.venv/bin/python manage.py solve \
  --file scripts/request_trace.artx \
  --context request \
  --output json
```

Use cached execution for repeated automation runs:

```bash
.venv/bin/python manage.py resolve \
  --file scripts/user_preview.artx \
  --context user
```

## Contributor onboarding for roots and actions

When adding new sigil roots or pipeline actions:

1. Register/update built-in root policy in `apps/sigils/builtin_policy.py` and ensure model-backed roots are present through fixtures/admin.
2. Implement parser/resolver behavior in `apps/sigils/sigil_resolver.py` using uppercase canonical action heads.
3. Add or update tests in `apps/sigils/tests/` for:
   - parsing behavior,
   - policy context gating (`admin`, `user`, `request`),
   - runtime resolution semantics.
4. Keep documentation synchronized:
   - `docs/development/expression-cookbook.md` for expression patterns, migration pairs, and context policy table.
   - this command guide for runnable CLI examples.
5. Refresh admin discoverability metadata in `apps/sigils/sigil_builder.py` and `apps/sigils/templates/admin/sigil_builder.html` so new roots/actions are filterable and copyable from `/admin/sigil-builder/`.

## Error behavior

The command exits non-zero for:

- parse errors (invalid `LET`/`EMIT` lines),
- policy errors (disallowed roots/actions for the selected context),
- runtime errors (unexpected execution failures).

Diagnostics remain concise, for example:

- `parse error: line 3: LET requires NAME = EXPR syntax`
- `policy error: line 1: expression blocked or unresolved`
