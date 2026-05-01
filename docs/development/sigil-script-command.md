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

## Portable suite documents

SIGILS are also the suite's portability mechanism for skills and documents that
are stored generally and written locally on each Arthexis device. A package can
store `[CONF.BASE_DIR]`, `[SYS.NODE_ROLE]`, or another allowed suite SIGIL in a
skill reference, script template, or agent instruction. The stored package keeps
the SIGIL text. The target device resolves it only when materializing the file to
its local directory.

This keeps portable documents reusable while still allowing local behavior. It
also sets a hard boundary: secrets and runtime state must not be moved through
SIGILS or package files. Store secrets on each node through that node's normal
credential path, and keep `workgroup.md` as local coordination state.

See [Codex Skill Packages](codex-skill-packages.md) for package materialization
and workgroup rules.

## Error behavior

The command exits non-zero for:

- parse errors (invalid `LET`/`EMIT` lines),
- policy errors (disallowed roots/actions for the selected context),
- runtime errors (unexpected execution failures).

Diagnostics remain concise, for example:

- `parse error: line 3: LET requires NAME = EXPR syntax`
- `policy error: line 1: expression blocked or unresolved`
