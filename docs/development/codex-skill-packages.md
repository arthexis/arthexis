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
`[CONF.BASE_DIR]` or `[SYS.NODE_ROLE]`.

Portable package storage keeps SIGILS unresolved. The suite resolves allowed
SIGILS only when package files are materialized into a local directory. This
lets one general skill package adapt to each node without embedding one
operator's local paths.

Default materialization allows non-secret local context:

- `SYS`: suite system metadata such as role or upgrade state.
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

## Proposed Node-To-Node Package Exchange

Issue #7618 tracks a node-to-node sharing flow for Codex skill packages. The
goal is to let one registered Arthexis node ask another node for its portable
skill and agent package data, preview the package, and import all skills or a
selected subset without committing operator skills into the source tree.

The provider node should expose a protected package export endpoint that returns
the same package format produced by:

```bash
python manage.py codex_skill_packages export --output skills.zip
```

The endpoint must use existing node trust boundaries. Anonymous callers must not
be able to download packages, and the endpoint must not introduce a second
package serializer. It should call the existing package export service so
excluded files remain redacted and portable payload rules stay centralized.

The requesting node should get a CLI workflow shaped like this:

```bash
python manage.py codex_skill_packages pull --node gateway --dry-run
python manage.py codex_skill_packages pull --node gateway --all
python manage.py codex_skill_packages pull --node gateway --slug quotation --slug rfid-scan
```

`--dry-run` should fetch the remote package and run the existing importer in
preview mode. `--all` should import every portable skill in the package. Repeated
`--slug` options should filter the package before import so an operator can pull
only the skills they want from a trusted node.

The admin workflow should mirror the upload import flow:

1. Staff chooses a registered node from an Agent Skills or Nodes admin action.
2. The suite fetches the remote package and shows a preview of skill slugs and
   manifest files.
3. Staff confirms either all skills or selected skills.
4. The suite applies the same `import_codex_skill_package(..., dry_run=False)`
   path used by upload import.

The exchange must keep the current safety boundary:

- `SKILL.md`, `agents/`, `assets/`, `references/`, `scripts/`, and `templates/`
  are portable when classification allows them.
- Runtime state, caches, generated archives, local workgroup state, and secrets
  are not transferred as payload content.
- Target-node materialization still resolves only the approved SIGILS for that
  node.
- Import preview and apply must reject unsafe paths, invalid UTF-8, malformed
  manifests, duplicate paths, missing `SKILL.md`, and unsupported package
  formats through the existing package service.

Implementation should add tests for successful all-skill import, selected-skill
import, unauthorized remote access, malformed remote package responses, and
excluded-file handling. Validation should include:

```bash
python manage.py test run -- apps.skills apps.nodes
python manage.py check --fail-level ERROR
python manage.py migrations check
python scripts/check_import_resolution.py apps.skills apps.nodes
git diff --check
```
