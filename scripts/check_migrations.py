#!/usr/bin/env python3
"""Verify migration state: ensure no new migrations required."""
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

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


def _run_manage(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "manage.py", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def _combine_process_output(result: subprocess.CompletedProcess[str]) -> str:
    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(result.stderr)
    return "\n".join(part.strip() for part in parts if part.strip())


def _report_failure(message: str, result: subprocess.CompletedProcess[str]) -> None:
    print(message, file=sys.stderr)
    combined = _combine_process_output(result)
    if combined:
        print(combined, file=sys.stderr)


def _check_migrations(labels: Iterable[str]) -> int:
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

    print(
        "Uncommitted model changes detected. Please rewrite the latest migration.",
        file=sys.stderr,
    )
    if combined:
        print(combined, file=sys.stderr)
    return 1


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    labels = _local_app_labels()
    return _check_migrations(labels)


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
