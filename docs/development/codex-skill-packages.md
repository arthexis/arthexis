# Codex Skill Packages

Arthexis stores Codex skills as portable package trees, not only as one `SKILL.md`
document. A package may include:

- `SKILL.md`
- `agents/`
- `assets/`
- `references/`
- `scripts/`
- `templates/`

Runtime state, caches, generated archives, and secrets are not portable package
content. The package scanner records excluded files as metadata when useful, but
does not store their sensitive payloads.

## SIGILS In Portable Documents

Use **SIGILS** when a suite-owned skill or document needs local customization on
each installed device. SIGILS are bracketed suite expressions such as
`[CONF.BASE_DIR]`, `[SYS.NODE_ROLE]`, or `[NODE.ROLE]`.

Portable package storage keeps SIGILS unresolved. The suite resolves allowed
SIGILS only when package files are materialized into a local directory. This
lets one general skill package adapt to each node without embedding one
operator's local paths.

Default materialization allows non-secret local context:

- `SYS`: suite system metadata such as role or upgrade state.
- `NODE`: local node records when the node sigil root is configured.
- A small allow-list of simple `CONF` keys such as `[CONF.BASE_DIR]` and
  `[CONF.NODE_ROLE]`.

Arbitrary `CONF` keys are not resolved during default materialization. Secret
settings such as `[CONF.SECRET_KEY]` remain literal text, and `ENV` SIGILS are
not allowed by default. Secrets must be configured independently on each node
and must never move through skill package export/import.

## Workgroup State

`workgroup.md` is local runtime coordination state. It must not be copied from
one device to another as package content. The suite exposes a workgroup service
boundary so future polling, status pages, or admin views can read the local file
without treating it as a portable document.

Use:

```bash
python manage.py codex_workgroup path
python manage.py codex_workgroup ensure
python manage.py codex_workgroup read
```

To materialize stored skill trees:

```bash
python manage.py codex_skill_packages materialize --target ~/.codex/skills
```

To preserve raw SIGILS for inspection instead of resolving them:

```bash
python manage.py codex_skill_packages materialize --target ~/.codex/skills --no-resolve-sigils
```

## Admin Package Import

Staff users with both add and change permission for Agent Skills can import a
Codex skill package from **Admin > Agent Skills > Import**.

The admin upload first previews the package with the same dry-run validation as
the command-line importer. The preview shows each skill slug and the number of
manifest files that will be processed. No `AgentSkill` or `AgentSkillFile` rows
are written until the operator confirms the preview.

Preview uploads are stored under the configured Django default storage backend
with a one-hour expiry, so the follow-up apply request can run on another web
worker as long as the deployment's default storage is shared by those workers.

The apply step calls the same package importer without dry-run mode. Package
service validation still owns the safety boundary: unsafe paths, invalid UTF-8,
unsupported manifests, blocked secrets, portability reclassification, and
soft-deleted slug restoration all follow the command-line import behavior.
