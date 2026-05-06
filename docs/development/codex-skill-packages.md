# Operator Framework Packages

Arthexis stores shareable operator framework records in three model families:

- **Skills**: compact, indexable knowledge that helps an OPERATOR or LLM-assisted
  session pick the right suite command, script, or OS tool.
- **Agents**: AGENTS.md context blocks selected by Node Role, enabled Node
  Features, enabled Suite Features, or default context.
- **Hooks**: deterministic commands for Codex-compatible session events.

There is no OPERATOR-vs-AGENT actor split in this framework. An OPERATOR is the
human using the LLM-assisted session. AGENT records are context for generated
AGENTS files, not personalities, operator identities, or alternate suite roles.

## Skills

Skill descriptions should fit in 720 characters or less so they can be searched,
printed, or stored on RFID cards. Keep the `SKILL.md` useful to a human reader,
but prefer suite commands or skill-bundled scripts for deterministic logic. A
common skill path should usually finish within four or five script or command
invocations; preserve complex routing rules only when they are genuinely useful
operator knowledge.

Portable skill packages may include:

- `SKILL.md`
- `agents/`
- `assets/`
- `references/`
- `scripts/`
- `templates/`

Runtime state, caches, generated archives, and secrets are not portable package
content. The package scanner records excluded files as metadata when useful, but
does not store their sensitive payloads.

## Agents

The repository root `AGENTS.md` is static developer guidance for the suite. Local
node operation uses a generated file at:

```bash
python manage.py codex_agents path
python manage.py codex_agents write
```

The generated AGENTS file is written to `work/codex/AGENTS.md` by default during
startup maintenance and upgrade transforms. Render order is:

1. Node Role context.
2. enabled Node Feature context.
3. enabled Suite Feature context.
4. default context.

Node Role context should carry the most specific rules because `NODE ROLE` is
the suite role boundary.

## Hooks

Hooks are deterministic command records. They declare an event, platform
(`any`, `linux`, or `windows`), command, environment, timeout, and the same role
or feature selectors used by Agent records. Hooks should contain coded behavior,
not prose recipes.

Launchers can inspect enabled hooks for the local node with:

```bash
python manage.py codex_hooks list --event session_start
python manage.py codex_hooks list --event before_command --platform windows
```

## SIGILS In Portable Documents

Use **SIGILS** when a suite-owned skill or document needs local customization on
each installed device. SIGILS are bracketed suite expressions such as
`[CONF.BASE_DIR]` or `[SYS.NODE_ROLE]`.

Portable package storage keeps SIGILS unresolved. The suite resolves allowed
SIGILS only when package files are materialized into a local directory. This
lets one general package adapt to each node without embedding one OPERATOR's
local paths.

Default materialization allows non-secret local context:

- `SYS`: suite system metadata such as role or upgrade state.
- a small allow-list of simple `CONF` keys such as `[CONF.BASE_DIR]` and
  `[CONF.NODE_ROLE]`.

Secrets must be configured independently on each node and must never move
through package export/import.

## Commands

To materialize stored skill trees:

```bash
python manage.py codex_skill_packages materialize --target ~/.codex/skills
```

To preserve raw SIGILS for inspection instead of resolving them:

```bash
python manage.py codex_skill_packages materialize --target ~/.codex/skills --no-resolve-sigils
```

To export/import the portable framework package:

```bash
python manage.py codex_skill_packages export --output operator-framework.zip
python manage.py codex_skill_packages import --package operator-framework.zip --dry-run
python manage.py codex_skill_packages import --package operator-framework.zip
```

Admin import is available from **Admin > Skills > Import** for staff with Skill
and Skill File add/change/delete permissions.

Validation should include:

```bash
python manage.py test run -- apps.skills
python manage.py check --fail-level ERROR
python manage.py migrations check
python scripts/check_import_resolution.py apps.skills
git diff --check
```
