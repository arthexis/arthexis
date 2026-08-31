"""Changed-file test planning and PR hygiene reports."""

from __future__ import annotations

import re
import shlex
import sys
from collections.abc import Iterable, Mapping
from typing import Any

MANAGE_PY = "manage.py"
README_RE = re.compile(r"(^|/)(README|README\.[^/]+)$", re.IGNORECASE)
INSTALL_HEALTH_OCPP_RE = re.compile(r"^(apps/ocpp/|tests/ocpp/)")
INSTALL_HEALTH_REST_RE = re.compile(
    r"^("
    r"\.importlinter|"
    r"\.github/workflows/install-health\.yml|"
    r"\.github/workflows/ci\.yml|"
    r"apps/|"
    r"config/|"
    r"scripts/|"
    r"tests/|"
    r"manage\.py|"
    r"pyproject\.toml|"
    r"requirements[^/]*\.txt|"
    r"install\.sh|"
    r"env-refresh\.sh|"
    r"upgrade\.sh"
    r")"
)
MAKEMIGRATIONS_CHECK_RE = re.compile(
    r"\bmakemigrations\b(?=[^\n`]*--check)(?=[^\n`]*--dry-run)",
    re.IGNORECASE,
)


def _is_django_model_path(path: str) -> bool:
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "apps":
        return False
    return parts[2] == "models.py" or parts[2] == "models"


def classify_changed_files(
    paths: Iterable[str],
) -> tuple[list[str], set[str], dict[str, bool], list[str]]:
    files = sorted({path for path in paths if path})
    apps: set[str] = set()
    flags = {
        "docs_only": bool(files),
        "migration_change": False,
        "model_change": False,
        "workflow_change": False,
    }
    generated_candidates: list[str] = []

    for path in files:
        if path.startswith("apps/"):
            flags["docs_only"] = False
            parts = path.split("/")
            if len(parts) > 1:
                apps.add(parts[1])
            if _is_django_model_path(path):
                flags["model_change"] = True
            if "/migrations/" in path:
                flags["migration_change"] = True
        elif not path.startswith("docs/") and not path.endswith(".md"):
            flags["docs_only"] = False
        if path.startswith(".github/workflows/"):
            flags["workflow_change"] = True
        if path.endswith((".pyc", ".sqlite3", ".log")) or "__pycache__" in path:
            generated_candidates.append(path)

    return files, apps, flags, generated_candidates


def build_test_commands(
    apps: set[str], flags: Mapping[str, bool], generated_candidates: list[str]
) -> tuple[list[list[str]], list[str]]:
    commands = [[sys.executable, MANAGE_PY, "check", "--fail-level", "ERROR"]]
    notes: list[str] = []
    test_paths = [f"apps/{app_label}/tests" for app_label in sorted(apps)]
    if test_paths:
        commands.append([sys.executable, MANAGE_PY, "test", "run", "--", *test_paths])
    if flags["model_change"] or flags["migration_change"]:
        if flags["migration_change"]:
            commands.append([sys.executable, "scripts/check_migration_conflicts.py"])
        commands.append(
            [sys.executable, MANAGE_PY, "makemigrations", "--check", "--dry-run"]
        )
        commands.append([sys.executable, MANAGE_PY, "migrate", "--check"])
    if flags["workflow_change"]:
        notes.append(
            "Workflow files changed; inspect GitHub Actions syntax and required checks."
        )
    if flags["docs_only"]:
        notes.append(
            "Docs-only change; app tests may be unnecessary beyond Django checks."
        )
    if generated_candidates:
        notes.append("Generated artifacts detected: " + ", ".join(generated_candidates))
    return commands, notes


def affected_install_shard(files: Iterable[str]) -> str:
    """Return the local install-health shard affected by changed files."""

    ocpp_changed = False
    rest_changed = False
    for path in files:
        if INSTALL_HEALTH_OCPP_RE.search(path):
            ocpp_changed = True
            continue
        if INSTALL_HEALTH_REST_RE.search(path):
            rest_changed = True
    if ocpp_changed and rest_changed:
        return "both"
    if ocpp_changed:
        return "ocpp"
    if rest_changed:
        return "rest"
    return "none"


def _shell_join(command: Iterable[str]) -> str:
    return shlex.join(str(part) for part in command)


def _github_action_plan(files: list[str], shard: str) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if shard == "none":
        actions.append(
            {
                "name": "Install Health Check / focused local install shard",
                "scope": "focused-pr",
                "selection": "none",
                "reason": "No install-health code or test paths were changed.",
            }
        )
    else:
        actions.append(
            {
                "name": "Install Health Check / focused local install shard",
                "scope": "focused-pr",
                "selection": shard,
                "reason": "Changed files match the focused install-health shard selector for local validation.",
            }
        )
    if any(path.startswith(".github/workflows/") for path in files):
        actions.append(
            {
                "name": "Workflow syntax/review",
                "scope": "focused-pr",
                "selection": "manual-review",
                "reason": "Workflow files changed.",
            }
        )
    actions.append(
        {
            "name": "Full release/main validation",
            "scope": "main-release",
            "selection": "unchanged",
            "reason": "Release publish and main-branch gates continue to run their full validation.",
        }
    )
    return actions


def changed_files_to_test_plan(paths: Iterable[str]) -> dict[str, Any]:
    """Map changed file paths to deterministic local validation commands."""

    files, apps, flags, generated_candidates = classify_changed_files(paths)
    commands, notes = build_test_commands(apps, flags, generated_candidates)
    shard = affected_install_shard(files)
    command_text = [_shell_join(command) for command in commands]
    return {
        "files": files,
        "apps": sorted(apps),
        "modelChange": flags["model_change"],
        "migrationChange": flags["migration_change"],
        "workflowChange": flags["workflow_change"],
        "docsOnly": flags["docs_only"],
        "affectedInstallShard": shard,
        "commands": commands,
        "commandText": command_text,
        "focusedValidation": {
            "scope": "focused-pr",
            "commands": commands,
            "commandText": command_text,
            "githubActions": [
                action
                for action in _github_action_plan(files, shard)
                if action["scope"] == "focused-pr"
            ],
        },
        "mainReleaseValidation": {
            "scope": "main-release",
            "unchanged": True,
            "githubActions": [
                action
                for action in _github_action_plan(files, shard)
                if action["scope"] == "main-release"
            ],
        },
        "notes": notes,
    }


def render_test_plan_markdown(plan: Mapping[str, Any]) -> str:
    """Render an affected validation plan for PR bodies and CI summaries."""

    files = [str(path) for path in plan.get("files") or []]
    apps = [str(app) for app in plan.get("apps") or []]
    command_text = [str(command) for command in plan.get("commandText") or []]
    focused = plan.get("focusedValidation")
    if not isinstance(focused, Mapping):
        focused = {}
    main_release = plan.get("mainReleaseValidation")
    if not isinstance(main_release, Mapping):
        main_release = {}
    focused_actions = [
        action
        for action in focused.get("githubActions") or []
        if isinstance(action, Mapping)
    ]
    main_actions = [
        action
        for action in main_release.get("githubActions") or []
        if isinstance(action, Mapping)
    ]

    lines = [
        "# Affected Validation Plan",
        "",
        f"- Changed files: `{len(files)}`",
        f"- Affected apps: `{', '.join(apps) if apps else 'none'}`",
        f"- Affected install shard: `{plan.get('affectedInstallShard') or 'none'}`",
        (
            "- Schema impact: "
            f"models={str(bool(plan.get('modelChange'))).lower()} "
            f"migrations={str(bool(plan.get('migrationChange'))).lower()}"
        ),
        f"- Docs only: `{str(bool(plan.get('docsOnly'))).lower()}`",
        "",
        "## Focused PR Validation",
        "",
    ]
    if command_text:
        lines.extend(f"- `{command}`" for command in command_text)
    else:
        lines.append("- No local commands selected.")
    for action in focused_actions:
        lines.append(
            "- "
            f"{action.get('name')}: `{action.get('selection')}`"
            f" ({action.get('reason')})"
        )

    lines.extend(["", "## Main/Release Validation", ""])
    if main_actions:
        for action in main_actions:
            lines.append(
                "- "
                f"{action.get('name')}: `{action.get('selection')}`"
                f" ({action.get('reason')})"
            )
    else:
        lines.append("- Existing main/release gates remain unchanged.")

    notes = [str(note) for note in plan.get("notes") or []]
    if notes:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in notes)

    if files:
        lines.extend(["", "## Changed Files", ""])
        lines.extend(f"- `{path}`" for path in files[:40])
        if len(files) > 40:
            lines.append(f"- ... {len(files) - 40} more")

    return "\n".join(lines).rstrip() + "\n"


def hygiene_report(pr: Mapping[str, Any], files: Iterable[str]) -> dict[str, Any]:
    """Return deterministic PR hygiene warnings and failures."""

    body = str(pr.get("body") or "")
    changed = sorted({path for path in files if path})
    warnings: list[str] = []
    failures: list[str] = []
    lower_body = body.lower()
    if "summary" not in lower_body:
        warnings.append("body:missing-summary")
    if "validation" not in lower_body and "test" not in lower_body:
        warnings.append("body:missing-validation")
    if not re.search(
        r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#\d+", body, re.IGNORECASE
    ):
        warnings.append("body:missing-issue-link")

    model_paths = [path for path in changed if _is_django_model_path(path)]
    migration_paths = [path for path in changed if "/migrations/" in path]
    if model_paths and not migration_paths:
        if MAKEMIGRATIONS_CHECK_RE.search(body) and "no changes detected" in lower_body:
            warnings.append("model-change:no-migration-validated")
        else:
            failures.append("model-change:missing-migration")
    if any(README_RE.search(path) for path in changed):
        warnings.append("readme:changed")
    generated = [
        path
        for path in changed
        if path.endswith((".pyc", ".sqlite3", ".log")) or "__pycache__" in path
    ]
    if generated:
        failures.append("generated-artifacts:" + ",".join(generated))
    if (
        changed
        and not any(path.startswith("docs/") for path in changed)
        and len(changed) > 5
    ):
        warnings.append("docs:not-updated")

    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "files": changed,
    }
