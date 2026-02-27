# Prompt Storage Workflow Advice

When implementing a user request, preserve the original intent by recording it in `apps/prompts/fixtures` as a `prompts.storedprompt` fixture entry.

## Required fields
- `prompt_text`: exact user request text (or the relevant distilled request).
- `initial_plan`: the first implementation plan refined from the request.
- `pr_reference`: PR identifier/URL (`PR-TBD` is acceptable before PR creation).
- `context`: JSON object with relevant files, constraints, and validation notes.

## Commit hygiene
1. Make your code changes.
2. Add or update a prompt fixture in `apps/prompts/fixtures`.
3. Stage both code and prompt fixture changes.
4. Run `python scripts/check_prompt_storage.py` (also enforced by pre-commit).
5. Update `pr_reference` when opening/updating the PR.
