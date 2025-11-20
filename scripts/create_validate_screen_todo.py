#!/usr/bin/env python3
"""Generate metadata for manual screenshot validation."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "core" / "fixtures"


def guess_title(slug: str) -> str:
    words = slug.replace("_", "-").split("-")
    return " ".join(word.capitalize() for word in words if word)


def build_record(
    slug: str, url: str, details: str, title: str | None
) -> list[dict[str, object]]:
    label = title or guess_title(slug)
    request = f"Validate screen {label}"
    created_on = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    return [
        {
            "model": "core.todo",
            "fields": {
                "request": request,
                "created_on": created_on,
                "url": url,
                "request_details": details,
            },
        }
    ]


def write_record(path: Path, data: list[dict[str, object]], force: bool) -> None:
    if path.exists() and not force:
        print(
            f"Validation record {path} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path.relative_to(REPO_ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="Spec slug the manual check relates to")
    parser.add_argument("--url", required=True, help="URL requiring manual validation")
    parser.add_argument(
        "--details", required=True, help="Additional validation context"
    )
    parser.add_argument("--title", help="Override the human friendly screen title")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing record"
    )
    args = parser.parse_args(argv)

    data = build_record(args.slug, args.url, args.details, args.title)
    fixture_path = FIXTURE_DIR / f"todos__validate_screen_{args.slug}.json"
    write_record(fixture_path, data, args.force)
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
