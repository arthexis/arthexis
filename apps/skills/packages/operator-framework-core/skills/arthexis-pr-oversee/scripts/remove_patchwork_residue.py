#!/usr/bin/env python3
"""Safely remove non-worktree patchwork residue directories on Windows."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("ARTHEXIS_PATCHWORK_DIR", Path.home() / "patchwork"))


def run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
    )


def is_git_worktree(path: Path) -> bool:
    proc = run_git(path, ["rev-parse", "--is-inside-work-tree"])
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


def is_reparse_dir(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        return bool(is_junction())
    return False


def ensure_inside_root(root: Path, target: Path) -> None:
    root_abs = root.resolve(strict=True)
    target_abs = target.resolve(strict=False).absolute()
    try:
        target_abs.relative_to(root_abs)
    except ValueError as exc:
        raise SystemExit(f"refusing outside-root path: {target}") from exc
    if target_abs == root_abs:
        raise SystemExit(f"refusing root path: {target}")


def remove_path(path: Path) -> None:
    if is_reparse_dir(path):
        path.rmdir()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def target_paths(root: Path, args: argparse.Namespace) -> list[Path]:
    paths = []
    for item in args.path:
        path = Path(item)
        paths.append(path if path.is_absolute() else root / path)
    paths.extend(root / f"arthexis-arthexis-pr-{number}" for number in args.pr)
    if not paths:
        raise SystemExit("provide --pr or --path")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--pr", action="append", default=[], help="PR number to remove.")
    parser.add_argument("--path", action="append", default=[], help="Explicit path under root.")
    parser.add_argument("--write", action="store_true", help="Actually remove residue.")
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    results: list[dict[str, str | bool]] = []
    for target in target_paths(root, args):
        ensure_inside_root(root, target)
        exists = target.exists()
        result: dict[str, str | bool] = {
            "path": str(target),
            "exists": exists,
            "removed": False,
        }
        if not exists:
            results.append(result)
            continue
        if is_git_worktree(target):
            result["error"] = "refusing active git worktree; use pr_oversee patchwork first"
            results.append(result)
            continue
        if args.write:
            remove_path(target)
            result["removed"] = True
        results.append(result)

    for result in results:
        print(result)
    return 1 if any("error" in result for result in results) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(1) from exc
