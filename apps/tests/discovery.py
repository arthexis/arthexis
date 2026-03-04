"""Helpers for discovering pytest suite metadata."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from django.conf import settings

DEFAULT_TEST_MARKERS = {
    "", "asyncio", "anyio", "ddtrace", "django_db", "filterwarnings", "parametrize", "skip", "skipif", "usefixtures", "xfail"
}


class TestDiscoveryError(RuntimeError):
    """Raised when pytest suite discovery cannot be completed."""


def discover_suite_tests(*, timeout: int = 120) -> list[dict[str, object]]:
    """Return normalized suite test metadata collected through pytest.

    Args:
        timeout: Number of seconds before subprocess collection is aborted.

    Returns:
        A list of dictionaries with node metadata suitable for ``SuiteTest``.

    Raises:
        TestDiscoveryError: If pytest collection fails or emits invalid JSON.
    """

    collector_script_path = Path(__file__).with_name("_pytest_collector.py")
    command = [sys.executable, str(collector_script_path)]

    try:
        result = subprocess.run(
            command,
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TestDiscoveryError(
            f"Pytest discovery timed out after {timeout} seconds in {settings.BASE_DIR}."
        ) from exc
    except OSError as exc:
        raise TestDiscoveryError(f"Unable to run pytest discovery: {exc}") from exc

    payload_text = (result.stdout or "").strip().splitlines()
    if not payload_text:
        raise TestDiscoveryError(result.stderr.strip() or "Pytest discovery produced no output.")

    try:
        payload = json.loads(payload_text[-1])
    except json.JSONDecodeError as exc:
        raise TestDiscoveryError("Pytest discovery produced invalid JSON output.") from exc

    if payload.get("returncode") not in (0, 5):
        stderr = (result.stderr or "").strip()
        raise TestDiscoveryError(stderr or f"Pytest discovery failed with exit code {payload.get('returncode')}.")

    base_dir = Path(settings.BASE_DIR)
    normalized: list[dict[str, object]] = []
    for raw in payload.get("items", []):
        node_id = str(raw.get("node_id", "")).strip()
        if not node_id:
            continue
        file_path = _normalize_file_path(base_dir, str(raw.get("file_path", "")))
        normalized.append(
            {
                "node_id": node_id[:512],
                "name": str(raw.get("name", ""))[:255],
                "module_path": str(raw.get("module_path", ""))[:512],
                "class_name": str(raw.get("class_name", ""))[:255],
                "marks": _normalize_marks(raw.get("marks", [])),
                "file_path": file_path[:512],
                "app_label": _infer_app_label(file_path),
                "is_parameterized": "[" in node_id and "]" in node_id,
            }
        )
    return normalized


def _normalize_file_path(base_dir: Path, raw_path: str) -> str:
    """Normalize a pytest-provided file path into a repository-relative path."""

    if not raw_path:
        return ""
    path = Path(raw_path)
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return raw_path


def _infer_app_label(file_path: str) -> str:
    """Infer app label from a repo-relative path in ``apps/<label>/...`` format."""

    if not file_path:
        return ""
    parts = Path(file_path).parts
    if len(parts) >= 2 and parts[0] == "apps":
        return parts[1]
    return ""


def _normalize_marks(raw_marks: object) -> list[str]:
    """Return sorted marker names without built-in pytest keywords."""

    if not isinstance(raw_marks, list):
        return []
    cleaned = {
        str(mark).strip()
        for mark in raw_marks
        if isinstance(mark, str) and str(mark).strip() and str(mark).strip() not in DEFAULT_TEST_MARKERS
    }
    return sorted(cleaned)
