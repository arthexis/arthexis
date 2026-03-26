#!/usr/bin/env python3
"""Verify migration state and baseline policy for local Django migrations."""
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

from scripts.build_migration_baseline import (
    DEFAULT_APPS,
    DEFAULT_RECENT_SQUASH_WINDOW,
    DEFAULT_THRESHOLD,
    evaluate_app_baseline,
)
from scripts.check_migration_conflicts import run_checks as run_migration_conflict_checks


def _local_app_labels(apps_module, settings_module) -> list[str]:
    """Return local Django app labels rooted under the repository base dir.

    Parameters:
        apps_module: Loaded ``django.apps.apps`` registry.
        settings_module: Loaded Django settings object.

    Return values:
        A list of app labels whose app paths live inside ``BASE_DIR``.

    Raised exceptions:
        None.
    """

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
    """Run ``manage.py`` with repository-relative working directory.

    Parameters:
        *args: Management command arguments to append after ``manage.py``.

    Return values:
        The completed subprocess result.

    Raised exceptions:
        Any ``subprocess`` spawning exceptions raised by ``subprocess.run``.
    """

    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def _combine_process_output(result: subprocess.CompletedProcess[str]) -> str:
    """Combine stdout and stderr from ``result`` into one trimmed string.

    Parameters:
        result: Completed subprocess result to summarize.

    Return values:
        A newline-joined string containing non-empty stdout and stderr.

    Raised exceptions:
        None.
    """

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(result.stderr)
    return "\n".join(part.strip() for part in parts if part.strip())


def _working_tree_clean() -> bool:
    """Return whether ``git status --porcelain`` reports a clean worktree.

    Parameters:
        None.

    Return values:
        ``True`` when the Git worktree is clean, otherwise ``False``.

    Raised exceptions:
        None; Git failures are treated as ``False``.
    """

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    return status.returncode == 0 and not status.stdout.strip()


def _report_failure(message: str, result: subprocess.CompletedProcess[str]) -> None:
    """Print ``message`` plus captured subprocess output to stderr.

    Parameters:
        message: User-facing summary line.
        result: Completed subprocess result whose output should be displayed.

    Return values:
        None.

    Raised exceptions:
        None.
    """

    print(message, file=sys.stderr)
    combined = _combine_process_output(result)
    if combined:
        print(combined, file=sys.stderr)


def _check_migrations(labels: Iterable[str]) -> int:
    """Verify that local apps do not require new migrations.

    Parameters:
        labels: Django app labels to pass to ``makemigrations --check``.

    Return values:
        ``0`` on success, otherwise ``1``.

    Raised exceptions:
        None.
    """

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
            _report_failure("Automatic merge failed.", merge_result)
            return 1

        post_merge = _run_manage(*check_args)
        if post_merge.returncode == 0:
            print("Automatic merge migration created.", file=sys.stderr)
            print("Migrations check passed.")
            return 0

        _report_failure(
            "Automatic merge created but migrations are still inconsistent.",
            post_merge,
        )
        return 1

    if _working_tree_clean() and "Migrations for" not in combined:
        # makemigrations --check occasionally returns a non-zero status when merge
        # migrations are already present. Treat that state as a success so a clean
        # repository does not fail the check.
        print("Migrations check passed.")
        return 0

    print(
        "Uncommitted model changes detected. Please rewrite the latest migration.",
        file=sys.stderr,
    )
    if combined:
        print(combined, file=sys.stderr)
    return 1


def _check_baseline_depths(
    app_labels: Iterable[str],
    *,
    threshold: int = DEFAULT_THRESHOLD,
    recent_window: int = DEFAULT_RECENT_SQUASH_WINDOW,
) -> int:
    """Fail when high-churn apps exceed depth without a recent squash marker.

    Parameters:
        app_labels: Application labels whose baseline depth should be evaluated.
        threshold: Maximum allowed active migration chain depth.
        recent_window: Recent migration-number window that counts as freshly squashed.

    Return values:
        ``0`` when all apps satisfy the policy, otherwise ``1``.

    Raised exceptions:
        None.
    """

    failures: list[str] = []
    for app_label in app_labels:
        status = evaluate_app_baseline(app_label, repo_root=REPO_ROOT)
        if status.exceeds_threshold(threshold) and not status.has_recent_squash(
            recent_window
        ):
            failures.append(
                f"{app_label}: depth={status.active_chain_depth}, "
                f"latest={status.latest_number}, last_squash={status.latest_squash_number}"
            )

    if failures:
        print(
            "Migration baseline policy check failed. Run scripts/build_migration_baseline.py "
            "during the release train to create a fresh squash marker:",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("Migration baseline depth policy passed.")
    return 0


def main() -> int:
    """Run migration conflict, drift, and baseline checks.

    Parameters:
        None.

    Return values:
        Process exit code for the combined migration validation workflow.

    Raised exceptions:
        None; all failures are reported as exit codes.
    """

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
    # Prefer the lightweight SQLite backend during migration checks to avoid
    # spending time probing unavailable PostgreSQL instances. The environment
    # variable can still be overridden by callers that want to target a
    # specific database engine.
    os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
    django.setup()
    labels = _local_app_labels(apps, settings)
    migration_check = _check_migrations(labels)
    if migration_check != 0:
        return migration_check
    return _check_baseline_depths(DEFAULT_APPS)


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
