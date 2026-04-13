from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat


@dataclass
class VerificationIssue:
    filename: str
    reason: str


def parse_expected_images(readme_path: Path) -> list[str]:
    if not readme_path.exists():
        return []

    expected: list[str] = []
    current_section = False
    for raw_line in readme_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## `") and line.endswith("`"):
            current_section = True
            continue
        if not current_section or not line.startswith("- **desktop**: ["):
            continue
        link = line.split("[", maxsplit=1)[1].split("]", maxsplit=1)[0].strip()
        expected.append(Path(link).name)
    return expected


def detect_visual_blank(image: Image.Image, stddev_floor: float) -> bool:
    stat = ImageStat.Stat(image.convert("RGB"))
    return max(stat.stddev) < stddev_floor


def verify_artifacts(
    artifacts_dir: Path,
    min_width: int,
    min_height: int,
    min_size_bytes: int,
    stddev_floor: float,
) -> tuple[list[VerificationIssue], list[str], list[str]]:
    images = sorted(artifacts_dir.glob("*.png"))
    issues: list[VerificationIssue] = []

    if not images:
        issues.append(VerificationIssue("(all)", "No screenshot PNG artifacts were produced."))
        return issues, [], []

    expected = parse_expected_images(artifacts_dir / "README.md")
    if expected:
        available = {path.name for path in images}
        missing = sorted(name for name in expected if name not in available)
        for filename in missing:
            issues.append(
                VerificationIssue(
                    filename,
                    "Listed in artifacts/README.md but missing from artifacts directory.",
                )
            )

    for image_path in images:
        if image_path.stat().st_size < min_size_bytes:
            issues.append(
                VerificationIssue(
                    image_path.name,
                    f"File is too small ({image_path.stat().st_size} bytes < {min_size_bytes} bytes).",
                )
            )
            continue

        with Image.open(image_path) as image:
            width, height = image.size

            if width < min_width or height < min_height:
                issues.append(
                    VerificationIssue(
                        image_path.name,
                        f"Image dimensions are too small ({width}x{height} < {min_width}x{min_height}).",
                    )
                )
                continue

            if detect_visual_blank(image, stddev_floor):
                issues.append(
                    VerificationIssue(
                        image_path.name,
                        "Image appears visually blank (near-zero color variance).",
                    )
                )

    return issues, [path.name for path in images], expected


def write_report(
    report_path: Path,
    artifact_names: list[str],
    expected: list[str],
    issues: list[VerificationIssue],
) -> None:
    lines = [
        "## Screenshot verification checklist",
        "",
        f"- {'✅' if artifact_names else '❌'} PNG artifacts found: `{len(artifact_names)}`",
        f"- {'✅' if not expected or all(name in artifact_names for name in expected) else '❌'} README-listed desktop screenshots present",
        f"- {'✅' if not any('dimensions' in i.reason for i in issues) else '❌'} Minimum dimensions check",
        f"- {'✅' if not any('too small' in i.reason for i in issues) else '❌'} Minimum file-size check",
        f"- {'✅' if not any('visually blank' in i.reason for i in issues) else '❌'} Non-blank image heuristic",
        "",
    ]

    if issues:
        lines.append("### Detected issues")
        lines.append("")
        for issue in issues:
            lines.append(f"- ❌ `{issue.filename}`: {issue.reason}")
    else:
        lines.append("All checklist checks passed.")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify generated screenshot artifacts in CI before publishing them to pull requests."
    )
    parser.add_argument("--artifacts-dir", default="artifacts")
    parser.add_argument("--report", default="artifacts/screenshot-verification.md")
    parser.add_argument("--min-width", type=int, default=320)
    parser.add_argument("--min-height", type=int, default=240)
    parser.add_argument("--min-size-bytes", type=int, default=8_000)
    parser.add_argument("--stddev-floor", type=float, default=2.0)
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    issues, artifact_names, expected = verify_artifacts(
        artifacts_dir=artifacts_dir,
        min_width=args.min_width,
        min_height=args.min_height,
        min_size_bytes=args.min_size_bytes,
        stddev_floor=args.stddev_floor,
    )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(report_path, artifact_names, expected, issues)

    print(report_path.read_text(encoding="utf-8"))

    if issues:
        print("\nScreenshot verification failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
