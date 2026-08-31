#!/usr/bin/env python
"""Compute a hash representing the current static files state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CACHE_DIR = BASE_DIR / ".cache"
CACHE_FILE = CACHE_DIR / "staticfiles_state.json"


def _iter_static_files():
    """Yield tuples describing collected static files.

    Each tuple contains the storage location (if available), the relative
    path within that storage, and the storage instance itself. The results are
    sorted to provide a stable order for hashing.
    """

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from config.loadenv import loadenv

        loadenv()
        import django

        django.setup()
    except Exception as exc:  # pragma: no cover - setup failures bubble up
        raise SystemExit(str(exc))

    try:
        from django.contrib.staticfiles.finders import get_finders
    except Exception as exc:  # pragma: no cover - import errors
        raise SystemExit(str(exc))

    files = []
    for finder in get_finders():
        try:
            file_iter = finder.list([])
        except Exception:
            continue
        for relative_path, storage in file_iter:
            location = getattr(storage, "location", "") or ""
            files.append((str(location), str(relative_path), storage))

    files.sort(key=lambda item: (item[0], item[1]))
    for item in files:
        yield item


def _stat_details(storage, relative_path: str) -> tuple[str | None, int | None]:
    """Return a signature for the static file and its modification time."""

    try:
        file_path = storage.path(relative_path)
    except (AttributeError, NotImplementedError, ValueError):
        file_path = None

    if file_path:
        try:
            stat = os.stat(file_path)
        except OSError:
            return None, None
        return f"{stat.st_mtime_ns}:{stat.st_size}", stat.st_mtime_ns

    file_hash = hashlib.md5()
    try:
        with storage.open(relative_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                if not chunk:
                    break
                file_hash.update(chunk)
    except OSError:
        return None, None
    return file_hash.hexdigest(), None


def _current_commit() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BASE_DIR)
            .decode()
            .strip()
        )
    except Exception:  # pragma: no cover - git may not be available
        return ""


def _load_cache() -> dict | None:
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _save_cache(payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _write_metadata(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _filesystem_snapshot(roots: Iterable[str]) -> tuple[int | None, int]:
    """Return the newest mtime and file count for provided roots.

    When no valid roots are present the timestamp is ``None`` to signal the
    cache cannot be used.
    """

    latest_mtime = 0
    file_count = 0
    saw_root = False

    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists():
            continue
        saw_root = True
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if "static" not in file_path.parts and root.name != "static":
                continue
            try:
                stat = file_path.stat()
            except OSError:
                continue
            file_count += 1
            latest_mtime = max(latest_mtime, stat.st_mtime_ns)

    if not saw_root:
        return None, file_count
    return (latest_mtime if file_count else 0), file_count


def _cache_is_valid(cache: dict | None, commit: str) -> tuple[bool, str | None]:
    if not cache or cache.get("cacheable") is not True:
        return False, None
    if cache.get("commit") != commit:
        return False, None

    latest_mtime, file_count = _filesystem_snapshot(cache.get("roots", []))
    if latest_mtime is None:
        return False, None

    if (
        latest_mtime == cache.get("latest_mtime_ns")
        and file_count == cache.get("file_count")
    ):
        return True, cache.get("hash")

    return False, None


def compute_staticfiles_hash() -> tuple[str, dict]:
    digest = hashlib.md5()
    latest_mtime_ns: int | None = 0
    file_count = 0
    cacheable = True
    roots: set[str] = set()

    for location, relative_path, storage in _iter_static_files():
        signature, mtime_ns = _stat_details(storage, relative_path)
        if signature is None:
            continue

        digest.update(location.encode("utf-8", "ignore"))
        digest.update(b"\0")
        digest.update(relative_path.encode("utf-8", "ignore"))
        digest.update(b"\0")
        digest.update(signature.encode("utf-8"))
        digest.update(b"\0")

        file_count += 1
        if location:
            roots.add(location)
        if mtime_ns is None:
            cacheable = False
        else:
            latest_mtime_ns = max(latest_mtime_ns or 0, mtime_ns)

    metadata = {
        "latest_mtime_ns": latest_mtime_ns if cacheable else None,
        "file_count": file_count,
        "roots": sorted(roots),
        "cacheable": cacheable and bool(file_count),
    }
    return digest.hexdigest(), metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Ignore the cache and recompute the hash.",
    )
    parser.add_argument(
        "--check-cache",
        action="store_true",
        help="Return the cached hash if still valid, otherwise exit with 3.",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        help="Optional path to write metadata describing the current snapshot.",
    )
    args = parser.parse_args(argv)

    commit = _current_commit()
    cache = None if args.ignore_cache else _load_cache()

    if not args.ignore_cache:
        cache_valid, cached_hash = _cache_is_valid(cache, commit)
        if cache_valid and cached_hash:
            if args.metadata_output:
                _write_metadata(args.metadata_output, cache)
            print(cached_hash)
            return 0
        if args.check_cache:
            return 3

    try:
        digest, metadata = compute_staticfiles_hash()
    except SystemExit as exc:
        message = str(exc)
        if message:
            print(message, file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive catch
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "hash": digest,
        "commit": commit,
        "latest_mtime_ns": metadata.get("latest_mtime_ns"),
        "file_count": metadata.get("file_count"),
        "roots": metadata.get("roots", []),
        "cacheable": metadata.get("cacheable", False),
    }
    _save_cache(payload)
    if args.metadata_output:
        _write_metadata(args.metadata_output, payload)

    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
