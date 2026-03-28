#!/usr/bin/env python3
"""Verify migration conflict and drift state for local Django migrations."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

from scripts.check_migration_conflicts import run_checks as run_migration_conflict_checks


def _local_app_labels(apps_module, settings_module) -> list[str]:
    """Return local Django app labels rooted under the repository base dir."""

    base_dir = Path(settings_module.BASE_DIR)
    labels: list[str] = []
    for app_config in apps_module.get_app_configs():
        try:
            Path(app_config.path).relative_to(base_dir)
        except ValueError:
            continue
        labels.append(app_config.label)
    return labels


def _run_manage(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``manage.py`` with repository-relative working directory."""

    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def _combine_process_output(result: subprocess.CompletedProcess[str]) -> str:
    """Combine stdout and stderr from ``result`` into one trimmed string."""

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(result.stderr)
    return "\n".join(part.strip() for part in parts if part.strip())


def _working_tree_clean() -> bool:
    """Return whether ``git status --porcelain`` reports a clean worktree."""

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    return status.returncode == 0 and not status.stdout.strip()


def _check_migrations(labels: Iterable[str]) -> int:
    """Verify that local apps do not require new migrations."""

    labels_list = list(labels)
    check_args = ("makemigrations", *labels_list, "--check", "--dry-run", "--noinput")
    check_result = _run_manage(*check_args)
    if check_result.returncode == 0:
        print("Migrations check passed.")
        return 0

    combined = _combine_process_output(check_result)
    if "Conflicting migrations detected" in combined:
        print(
            "Conflicting migrations detected; attempting automatic merge.",
            file=sys.stderr,
        )
        merge_result = _run_manage("makemigrations", *labels_list, "--merge", "--noinput")
        if merge_result.returncode != 0:
            print("Automatic merge failed.", file=sys.stderr)
            merge_output = _combine_process_output(merge_result)
            if merge_output:
                print(merge_output, file=sys.stderr)
            return 1

        post_merge = _run_manage(*check_args)
        if post_merge.returncode == 0:
            print("Automatic merge migration created.", file=sys.stderr)
            print("Migrations check passed.")
            return 0

        print(
            "Automatic merge created but migrations are still inconsistent.",
            file=sys.stderr,
        )
        post_merge_output = _combine_process_output(post_merge)
        if post_merge_output:
            print(post_merge_output, file=sys.stderr)
        return 1

    if _working_tree_clean() and "Migrations for" not in combined:
        print("Migrations check passed.")
        return 0

    print(
        "Uncommitted model changes detected. Please rewrite the latest migration.",
        file=sys.stderr,
    )
    if combined:
        print(combined, file=sys.stderr)
    return 1


def main() -> int:
    """Run migration conflict and drift checks."""

    try:
        conflict_check = run_migration_conflict_checks(REPO_ROOT)
    except Exception as exc:
        print(f"Migration conflict pre-check failed: {exc}", file=sys.stderr)
        return 1
    if conflict_check != 0:
        return conflict_check

    try:
        import django
        from django.apps import apps
        from django.conf import settings
    except ModuleNotFoundError:
        print(
            "Django is required to run migration checks. Install project dependencies",
            file=sys.stderr,
        )
        return 1

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
    django.setup()
    labels = _local_app_labels(apps, settings)
    return _check_migrations(labels)


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
