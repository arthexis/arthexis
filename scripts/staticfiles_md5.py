#!/usr/bin/env python
"""Compute a hash representing the current static files state."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


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


def _stat_signature(storage, relative_path: str) -> str | None:
    """Return a string signature for the given static file.

    When the file is backed by a filesystem storage we use the modification
    time and size to avoid reading the file contents. If the storage does not
    expose a filesystem path we fall back to hashing the file contents.
    """

    try:
        file_path = storage.path(relative_path)
    except (AttributeError, NotImplementedError, ValueError):
        file_path = None

    if file_path:
        try:
            stat = os.stat(file_path)
        except OSError:
            return None
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    file_hash = hashlib.md5()
    try:
        with storage.open(relative_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                if not chunk:
                    break
                file_hash.update(chunk)
    except OSError:
        return None
    return file_hash.hexdigest()


def compute_staticfiles_hash() -> str:
    digest = hashlib.md5()
    for location, relative_path, storage in _iter_static_files():
        signature = _stat_signature(storage, relative_path)
        if signature is None:
            continue
        digest.update(location.encode("utf-8", "ignore"))
        digest.update(b"\0")
        digest.update(relative_path.encode("utf-8", "ignore"))
        digest.update(b"\0")
        digest.update(signature.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    try:
        digest = compute_staticfiles_hash()
    except SystemExit as exc:
        message = str(exc)
        if message:
            print(message, file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive catch
        print(str(exc), file=sys.stderr)
        return 1

    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
