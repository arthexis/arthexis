#!/usr/bin/env python3
"""Verify migration state: ensure no new migrations required and no merge migrations present."""
import os
import subprocess
import sys
from pathlib import Path

import django
from django.apps import apps
from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))


def _local_app_labels() -> list[str]:
    base_dir = Path(settings.BASE_DIR)
    labels: list[str] = []
    for app_config in apps.get_app_configs():
        try:
            Path(app_config.path).relative_to(base_dir)
        except ValueError:
            continue
        labels.append(app_config.label)
    return labels


def _run_makemigrations_check(labels: list[str]) -> subprocess.CompletedProcess:
    """Run ``makemigrations --check --dry-run`` and return the completed process."""

    return subprocess.run(
        ["python", "manage.py", "makemigrations", *labels, "--check", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def main() -> int:
    # Detect merge migrations
    known_merges = {REPO_ROOT / "core" / "migrations" / "0009_merge_20250901_2230.py"}
    for path in REPO_ROOT.rglob("migrations/*merge*.py"):
        if path not in known_merges:
            print(f"Merge migrations detected: {path}", file=sys.stderr)
            return 1

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    labels = _local_app_labels()

    # Ensure no new migrations are needed
    try:
        _run_makemigrations_check(labels)
    except subprocess.CalledProcessError as err:
        combined_output = (err.stdout or "") + (err.stderr or "")
        if "Conflicting migrations detected" in combined_output:
            print(
                "Conflicting migrations detected; attempting automatic merge.",
                file=sys.stderr,
            )
            merge_result = subprocess.run(
                ["python", "manage.py", "makemigrations", "--merge", "--noinput"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            if merge_result.returncode != 0:
                if merge_result.stdout:
                    sys.stderr.write(merge_result.stdout)
                if merge_result.stderr:
                    sys.stderr.write(merge_result.stderr)
                print(
                    "Automatic merge failed. Please resolve migration conflicts manually.",
                    file=sys.stderr,
                )
                return 1
            try:
                _run_makemigrations_check(labels)
            except subprocess.CalledProcessError as follow_err:
                if follow_err.stdout:
                    sys.stderr.write(follow_err.stdout)
                if follow_err.stderr:
                    sys.stderr.write(follow_err.stderr)
                print(
                    "Uncommitted model changes detected. Please rewrite the latest migration.",
                    file=sys.stderr,
                )
                return 1
        else:
            if err.stdout:
                sys.stderr.write(err.stdout)
            if err.stderr:
                sys.stderr.write(err.stderr)
            print(
                "Uncommitted model changes detected. Please rewrite the latest migration.",
                file=sys.stderr,
            )
            return 1

    print("Migrations check passed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
