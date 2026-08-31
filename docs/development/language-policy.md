# Arthexis Language Policy for 1.0.0

Arthexis 1.0.0 treats English as the canonical language for Suite-owned repository prose. Public repository documentation, release policy, source-adjacent explanations, and operator-facing developer guidance should stay readable to English-speaking contributors and maintainers.

README files and `docs/**` must retain English coverage. Translations are allowed when they are useful, but they are optional companion material and must not become release blockers. If a guide includes translated prose, the English version is the source of truth unless a maintainer explicitly documents a different authority for that file.

Source-adjacent prose should stay in English when the text is maintained by this repository: code comments, docstrings, management-command help text, generated Suite templates, and inline explanatory text should use clear technical English unless external compatibility or user-provided content requires otherwise.

Preserve these surfaces unless a maintainer explicitly approves a wording change:

- code identifiers, import paths, package metadata, migration names, protocol field names, and public API names;
- third-party license text, upstream quotes, external product names, and legal notices;
- generated content that must match an upstream format;
- user-provided data, fixtures that model external APIs, and compatibility strings;
- translations that are clearly marked as optional companion material.

Review expectations:

- keep English coverage complete for maintained README and `docs/**` prose;
- review translated or non-English source-adjacent prose for an English canonical equivalent;
- keep technical meaning ahead of literal translation when updating companion translations;
- document exemptions near the relevant inventory entry or policy batch;
- run `python3 scripts/language_policy_inventory.py --format markdown` before 1.0.0 readiness review.

## Inventory Classes

The deterministic inventory script classifies prose surfaces as:

- `english`: English text is detected and satisfies the 1.0.0 language policy.
- `missing-english`: README/docs prose appears non-English and needs English coverage.
- `source-adjacent-needs-english-review`: source-adjacent prose appears non-English and should be reviewed for an English canonical equivalent.
- `needs-review`: README/docs prose was detected without enough language signal to classify automatically.
- `preserve`: the surface does not currently need policy work or should be preserved.

The script is advisory before 1.0.0, and `--strict` only enforces English coverage gaps:

```bash
python3 scripts/language_policy_inventory.py --format markdown
python3 scripts/language_policy_inventory.py --format json
```
