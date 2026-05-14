#!/usr/bin/env python3
"""Inspect the local publish workflow for release triggers and publishing steps."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def default_checkout() -> Path:
    return Path(os.environ.get("ARTHEXIS_REPO", Path.home() / "Repos" / "arthexis")).expanduser()


def find_workflow(checkout: Path) -> Path | None:
    workflows = checkout / ".github" / "workflows"
    for name in ("publish.yml", "publish.yaml", "release.yml", "release.yaml"):
        path = workflows / name
        if path.exists():
            return path
    return None


def inspect(checkout: Path) -> dict[str, Any]:
    path = find_workflow(checkout)
    if not path:
        return {"checkout": str(checkout), "ok": False, "error": "publish workflow not found"}
    text = path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    checks = {
        "tag_trigger_hint": "tags:" in lower or "refs/tags" in lower or "v*" in lower,
        "github_release_hint": "gh release" in lower or "softprops/action-gh-release" in lower or "release:" in lower,
        "pypi_hint": "pypi" in lower or "twine" in lower or "pypa/gh-action-pypi-publish" in lower,
        "manual_dispatch_hint": "workflow_dispatch" in lower,
    }
    requirements = {
        "tag_trigger": checks["tag_trigger_hint"],
        "publish_target": checks["github_release_hint"] or checks["pypi_hint"],
    }
    return {
        "checkout": str(checkout),
        "workflow": str(path),
        "ok": all(requirements.values()),
        "checks": checks,
        "requirements": requirements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=default_checkout())
    args = parser.parse_args()
    result = inspect(args.checkout.resolve())
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
