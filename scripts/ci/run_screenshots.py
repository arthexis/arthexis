#!/usr/bin/env python3
"""Run screenshot specs and summarise the results for CI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import django

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from pages.screenshot_specs import (  # noqa: E402  - Django setup required
    ScreenshotSpec,
    ScreenshotSpecRunner,
    ScreenshotUnavailable,
    autodiscover,
    registry,
)


@dataclass
class ManualSummary:
    slug: str
    reason: str
    url: str | None = None


@dataclass
class ValidationSummary:
    spec: ScreenshotSpec
    image_path: Path
    base64_path: Path
    base64_data: str


def git_changed_files(base_ref: str | None) -> list[str]:
    try:
        if base_ref:
            merge_base = ""
            for ref in (base_ref, f"origin/{base_ref}"):
                if not ref:
                    continue
                try:
                    merge_base = subprocess.check_output(
                        ["git", "merge-base", ref, "HEAD"],
                        text=True,
                        stderr=subprocess.DEVNULL,
                    ).strip()
                except subprocess.CalledProcessError:
                    continue
                if merge_base:
                    break
            diff_range = f"{merge_base}..HEAD" if merge_base else "HEAD"
        else:
            diff_range = "HEAD"
        diff = subprocess.check_output(
            ["git", "diff", "--name-only", diff_range],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return []
    return [line.strip() for line in diff.splitlines() if line.strip()]


def select_specs(
    explicit: list[str], changed: Iterable[str], run_all: bool
) -> list[ScreenshotSpec]:
    autodiscover()
    if explicit:
        return [registry.get(slug) for slug in explicit]
    specs = registry.all()
    if run_all:
        return sorted(specs, key=lambda spec: spec.slug)
    changed_list = list(changed)
    return sorted(
        [spec for spec in specs if spec.matches(changed_list)],
        key=lambda spec: spec.slug,
    )


def write_summary(
    validated: list[ValidationSummary],
    manual: list[ManualSummary],
    errors: list[str],
    output_dir: Path,
) -> None:
    summary_lines: list[str] = ["## UI Screenshot Summary", ""]
    if validated:
        summary_lines.append("### Automated validations")
        summary_lines.append("")
        for item in validated:
            summary_lines.append(f"- `{item.spec.slug}` → `{item.image_path}`")
            summary_lines.append("")
            summary_lines.append(
                f"![{item.spec.slug}](data:image/png;base64,{item.base64_data})"
            )
            summary_lines.append("")
    if manual:
        summary_lines.append("### Pending manual validation")
        summary_lines.append("")
        for item in manual:
            summary_lines.append(f"- `{item.slug}` → {item.reason}")
            if item.url:
                summary_lines.append(f"  - URL: {item.url}")
    if errors:
        summary_lines.append("### Errors")
        summary_lines.append("")
        for err in errors:
            summary_lines.append(f"- {err}")
    summary_text = "\n".join(summary_lines).strip() + "\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(summary_text)
    (output_dir / "summary.md").write_text(summary_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run declarative screenshot specs")
    parser.add_argument("--base-ref", help="Base reference for git diff", default=None)
    parser.add_argument(
        "--changed-files", nargs="*", help="Explicit list of changed files"
    )
    parser.add_argument("--spec", action="append", dest="specs", default=[])
    parser.add_argument("--output-dir", default="artifacts/ui-screens")
    parser.add_argument("--run-all", action="store_true")
    args = parser.parse_args(argv)

    changed_files = args.changed_files or git_changed_files(args.base_ref)
    selected = select_specs(args.specs, changed_files, args.run_all)
    output_dir = (REPO_ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not selected:
        print("No screenshot specs selected.")
        (output_dir / "summary.md").write_text(
            "No screenshot specs selected.\n", encoding="utf-8"
        )
        return 0

    validated: list[ValidationSummary] = []
    manual: list[ManualSummary] = []
    errors: list[str] = []
    exit_code = 0

    with ScreenshotSpecRunner(output_dir) as runner:
        for spec in selected:
            reason = spec.manual_reason
            if reason:
                manual.append(
                    ManualSummary(slug=spec.slug, reason=reason, url=spec.url)
                )
                print(f"Manual validation recorded for {spec.slug}: {reason}")
                continue
            try:
                result = runner.run(spec)
            except ScreenshotUnavailable as exc:
                manual.append(
                    ManualSummary(slug=spec.slug, reason=str(exc), url=spec.url)
                )
                print(f"Manual validation recorded for {spec.slug}: {exc}")
                continue
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{spec.slug}: {exc}")
                print(f"Error running spec {spec.slug}: {exc}", file=sys.stderr)
                exit_code = 1
                continue
            base64_data = result.base64_path.read_text(encoding="utf-8")
            validated.append(
                ValidationSummary(
                    spec=spec,
                    image_path=result.image_path.relative_to(output_dir),
                    base64_path=result.base64_path.relative_to(output_dir),
                    base64_data=base64_data,
                )
            )
            print(f"Validated {spec.slug}: {result.image_path}")

    write_summary(validated, manual, errors, output_dir)
    (output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "validated": [item.spec.slug for item in validated],
                "manual": [item.slug for item in manual],
                "errors": errors,
                "changed_files": changed_files,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
