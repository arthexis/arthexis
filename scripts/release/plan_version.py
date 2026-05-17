"""Plan the next release VERSION from repository changes and PyPI state."""

from __future__ import annotations

import argparse
import json
import math
import re
import secrets
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_PACKAGE_NAME = "arthexis"
PYPI_USER_AGENT = "arthexis-release-planner"
SEMVER_RE = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


class BumpLevel(IntEnum):
    """Release bump levels ordered by policy severity."""

    PATCH = 1
    MINOR = 2
    MAJOR = 3

    @classmethod
    def from_name(cls, value: str) -> BumpLevel:
        normalized = value.strip().lower()
        if normalized == "patch":
            return cls.PATCH
        if normalized == "minor":
            return cls.MINOR
        if normalized == "major":
            return cls.MAJOR
        raise ValueError(f"Unsupported bump level: {value!r}")

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True, order=True)
class Version:
    """A strict MAJOR.MINOR.PATCH version."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> Version:
        match = SEMVER_RE.match(value.strip())
        if not match:
            raise ValueError(f"Cannot parse MAJOR.MINOR.PATCH version from {value!r}.")
        groups = match.groupdict()
        return cls(
            major=int(groups["major"]),
            minor=int(groups["minor"]),
            patch=int(groups["patch"]),
        )

    def bump(self, level: BumpLevel) -> Version:
        if level == BumpLevel.MAJOR:
            return Version(self.major + 1, 0, 0)
        if level == BumpLevel.MINOR:
            return Version(self.major, self.minor + 1, 0)
        return Version(self.major, self.minor, self.patch + 1)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class FileChange:
    """One changed file as reported by git diff --name-status."""

    status: str
    path: str
    old_path: str = ""
    patch: str = ""


@dataclass
class VersionPlan:
    """Structured release version decision."""

    current_version: str
    next_version: str
    required_bump: str
    version_bumped: bool
    release_needed: bool
    base_ref: str
    head_ref: str
    latest_release_version: str
    change_count: int
    reasons: list[str]
    summary_markdown: str


def plan_release_version(
    *,
    root: Path,
    package_name: str = DEFAULT_PACKAGE_NAME,
    version_file: Path = Path("VERSION"),
    base_ref: str = "",
    head_ref: str = "HEAD",
    bump_level: str = "auto",
    published_versions: Iterable[str] = (),
    pypi_timeout: float = 15.0,
    skip_pypi: bool = False,
) -> VersionPlan:
    """Return the next VERSION that should be reviewed for release."""

    root = root.resolve()
    current_version = Version.parse(_read_required_child_text(root, version_file))

    changes = collect_git_changes(root=root, base_ref=base_ref, head_ref=head_ref)
    app_sets = collect_git_app_sets(root=root, base_ref=base_ref, head_ref=head_ref)
    required_bump, reasons = determine_required_bump(
        changes,
        app_sets=app_sets,
        requested_level=bump_level,
    )

    latest_release_version = _parse_optional_version(base_ref)
    published = {
        Version.parse(version)
        for version in published_versions
        if _is_parseable_version(version)
    }
    if not skip_pypi:
        published.update(
            fetch_pypi_versions(
                package_name=package_name,
                package_version=str(current_version),
                timeout=pypi_timeout,
            )
        )

    release_needed = bool(changes) or not base_ref
    if not release_needed:
        next_version = current_version
    else:
        next_version = resolve_next_version(
            current_version=current_version,
            latest_release_version=latest_release_version,
            published_versions=published,
            required_bump=required_bump,
        )

    summary = render_summary(
        current_version=current_version,
        next_version=next_version,
        required_bump=required_bump,
        release_needed=release_needed,
        base_ref=base_ref,
        head_ref=head_ref,
        latest_release_version=latest_release_version,
        change_count=len(changes),
        reasons=reasons,
    )
    return VersionPlan(
        current_version=str(current_version),
        next_version=str(next_version),
        required_bump=required_bump.label,
        version_bumped=next_version != current_version,
        release_needed=release_needed,
        base_ref=base_ref,
        head_ref=head_ref,
        latest_release_version=str(latest_release_version)
        if latest_release_version
        else "",
        change_count=len(changes),
        reasons=reasons,
        summary_markdown=summary,
    )


def determine_required_bump(
    changes: Sequence[FileChange],
    *,
    app_sets: tuple[set[str], set[str]] | None = None,
    requested_level: str = "auto",
) -> tuple[BumpLevel, list[str]]:
    """Classify the minimum bump required by the documented maturity policy."""

    if requested_level.strip().lower() != "auto":
        level = BumpLevel.from_name(requested_level)
        return level, [f"{level.label.upper()}: requested by workflow input."]

    reasons: list[tuple[BumpLevel, str]] = []

    if app_sets is not None:
        before_apps, after_apps = app_sets
        for app in sorted(after_apps - before_apps):
            reasons.append((BumpLevel.MAJOR, f"MAJOR: app added: apps/{app}."))
        for app in sorted(before_apps - after_apps):
            reasons.append((BumpLevel.MAJOR, f"MAJOR: app removed: apps/{app}."))

    for change in changes:
        if _is_app_manifest_add_or_delete(change):
            status_name = "added" if change.status == "A" else "deleted"
            reasons.append(
                (
                    BumpLevel.MAJOR,
                    f"MAJOR: app manifest {status_name}: {change.path}.",
                )
            )
        elif _is_minor_contract_change(change):
            reasons.append((BumpLevel.MINOR, f"MINOR: public contract path: {change.path}."))
        elif _migration_creates_or_deletes_model(change):
            reasons.append((BumpLevel.MINOR, f"MINOR: model lifecycle migration: {change.path}."))

    if not reasons:
        return BumpLevel.PATCH, ["PATCH: no major or minor policy trigger detected."]

    level = max(level for level, _reason in reasons)
    return level, _dedupe(reason for reason_level, reason in reasons if reason_level == level)


def collect_git_changes(*, root: Path, base_ref: str, head_ref: str) -> list[FileChange]:
    """Collect changed files between base_ref and head_ref."""

    if not base_ref:
        return []

    diff_range = f"{base_ref}...{head_ref}"
    output = _git_stdout(
        ["git", "diff", "--name-status", "--find-renames", diff_range],
        cwd=root,
    )
    changes: list[FileChange] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        status = parts[0]
        old_path = ""
        path = ""
        if status.startswith("R") or status.startswith("C"):
            if len(parts) >= 3:
                old_path = _normalize_path(parts[1])
                path = _normalize_path(parts[2])
        elif len(parts) >= 2:
            path = _normalize_path(parts[1])
        if not path:
            continue
        patch = ""
        if re.fullmatch(r"apps/[^/]+/migrations/\d+_[^/]+\.py", path):
            patch = _git_stdout(
                ["git", "diff", "--unified=0", diff_range, "--", path],
                cwd=root,
            )
        changes.append(
            FileChange(
                status=status[:1],
                old_path=old_path,
                path=path,
                patch=patch,
            )
        )
    return changes


def collect_git_app_sets(
    *,
    root: Path,
    base_ref: str,
    head_ref: str,
) -> tuple[set[str], set[str]] | None:
    """Return app sets for base and head using manifest.py as app identity."""

    if not base_ref:
        return None
    return (
        _collect_app_manifests(root=root, ref=base_ref),
        _collect_app_manifests(root=root, ref=head_ref),
    )


def resolve_next_version(
    *,
    current_version: Version,
    latest_release_version: Version | None,
    published_versions: set[Version],
    required_bump: BumpLevel,
) -> Version:
    """Resolve the next unpublished version satisfying the required bump."""

    published_or_released = set(published_versions)
    if latest_release_version is not None:
        published_or_released.add(latest_release_version)

    if published_or_released:
        baseline = max(published_or_released)
        minimum = baseline.bump(required_bump)
    else:
        minimum = current_version

    candidate = current_version if current_version >= minimum else minimum
    while candidate in published_versions:
        candidate = candidate.bump(required_bump)
    return candidate


def fetch_pypi_versions(
    *,
    package_name: str,
    package_version: str,
    timeout: float,
) -> set[Version]:
    """Return parseable existing versions from the PyPI JSON API."""

    if not math.isfinite(timeout) or timeout <= 0:
        raise SystemExit(f"PyPI timeout must be a finite value greater than zero seconds: {timeout}.")
    pypi_url = f"https://pypi.org/pypi/{quote(package_name, safe='')}/json"
    request = Request(
        pypi_url,
        headers={"User-Agent": f"{PYPI_USER_AGENT}/{package_version}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            payload = {"releases": {}}
        else:
            raise SystemExit(
                f"Unable to verify existing PyPI versions: HTTP {exc.code} from {pypi_url}."
            ) from exc
    except URLError as exc:
        raise SystemExit(
            f"Network failure while checking PyPI for an existing release: {exc.reason}."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"Received invalid JSON from PyPI: {exc}.") from exc

    if not isinstance(payload, dict):
        raise SystemExit(f"Unexpected PyPI payload type: {type(payload).__name__}.")
    releases = payload.get("releases") or {}
    if not isinstance(releases, dict):
        raise SystemExit(f"Unexpected 'releases' payload type from PyPI: {type(releases).__name__}.")

    versions = set()
    for value in releases:
        if _is_parseable_version(value):
            versions.add(Version.parse(value))
    return versions


def render_summary(
    *,
    current_version: Version,
    next_version: Version,
    required_bump: BumpLevel,
    release_needed: bool,
    base_ref: str,
    head_ref: str,
    latest_release_version: Version | None,
    change_count: int,
    reasons: Sequence[str],
) -> str:
    """Return a markdown summary suitable for PR bodies and workflow summaries."""

    lines = [
        "### Release version plan",
        "",
        f"- Current VERSION: `{current_version}`",
        f"- Planned VERSION: `{next_version}`",
        f"- Required bump: `{required_bump.label}`",
        f"- Version file change needed: `{'yes' if next_version != current_version else 'no'}`",
        f"- Release changes detected: `{'yes' if release_needed else 'no'}`",
        f"- Base ref: `{base_ref or 'none'}`",
        f"- Head ref: `{head_ref}`",
        f"- Latest released version: `{latest_release_version or 'none'}`",
        f"- Changed files considered: `{change_count}`",
        "",
        "#### Policy reasons",
    ]
    lines.extend(f"- {reason}" for reason in reasons[:10])
    if len(reasons) > 10:
        lines.append(f"- And {len(reasons) - 10} more reason(s).")
    return "\n".join(lines)


def write_version_file(*, root: Path, version_file: Path, version: str) -> None:
    path = _resolve_child_path(root, version_file, label="version file")
    path.write_text(f"{version}\n", encoding="utf-8")


def write_github_output(plan: VersionPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    delimiter = _github_output_delimiter(plan.summary_markdown)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"summary_markdown<<{delimiter}\n")
        handle.write(plan.summary_markdown.rstrip() + "\n")
        handle.write(f"{delimiter}\n")
        handle.write(f"current_version={plan.current_version}\n")
        handle.write(f"next_version={plan.next_version}\n")
        handle.write(f"bump_level={plan.required_bump}\n")
        handle.write(f"version_bumped={str(plan.version_bumped).lower()}\n")
        handle.write(f"release_needed={str(plan.release_needed).lower()}\n")
        handle.write(f"previous_version={plan.current_version}\n")
        handle.write(f"simulated_version={plan.next_version}\n")


def emit_plan(
    plan: VersionPlan,
    *,
    github_output: Path | None = None,
    summary_file: Path | None = None,
    json_output: bool = False,
    stdout: Any = sys.stdout,
) -> None:
    if github_output is not None:
        write_github_output(plan, github_output)
    if summary_file is not None:
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(plan.summary_markdown + "\n", encoding="utf-8")
    if json_output:
        payload = asdict(plan)
        stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        stdout.write(plan.summary_markdown + "\n")


def _is_app_manifest_add_or_delete(change: FileChange) -> bool:
    return (
        change.status in {"A", "D"}
        and re.fullmatch(r"apps/[^/]+/manifest\.py", change.path) is not None
    )


def _is_minor_contract_change(change: FileChange) -> bool:
    path = change.path
    if _is_patch_only_path(path):
        return False
    if re.fullmatch(r"apps/[^/]+/(views|forms|models|consumers|apis|api|serializers)\.py", path):
        return True
    if re.match(r"apps/[^/]+/(views|forms|models|templates|static|consumers|apis|api|serializers)/", path):
        return True
    if re.fullmatch(r"apps/[^/]+/(urls|routes|routing)\.py", path):
        return True
    if re.match(r"config/(settings|urls|asgi|wsgi|celery)(\.py|/)", path):
        return True
    if path in {".env.example", "env.example", "sample.env"}:
        return True
    return False


def _migration_creates_or_deletes_model(change: FileChange) -> bool:
    if not re.fullmatch(r"apps/[^/]+/migrations/\d+_[^/]+\.py", change.path):
        return False
    return any(
        marker in change.patch
        for marker in (
            "migrations.CreateModel",
            "migrations.DeleteModel",
            "CreateModel(",
            "DeleteModel(",
        )
    )


def _is_patch_only_path(path: str) -> bool:
    if path.startswith(".github/"):
        return True
    if path.startswith("docs/") or path.startswith("tests/"):
        return True
    if re.match(r"apps/[^/]+/(tests|admin)(/|\.py)", path):
        return True
    if re.match(r"apps/[^/]+/(templates|static)(/[^/]+)*/admin/", path):
        return True
    if "/tests/" in path or path.endswith("_test.py") or path.startswith("scripts/"):
        return True
    return False


def _collect_app_manifests(*, root: Path, ref: str) -> set[str]:
    output = _git_stdout(["git", "ls-tree", "-r", "--name-only", ref, "--", "apps"], cwd=root)
    apps = set()
    for line in output.splitlines():
        match = re.fullmatch(r"apps/([^/]+)/manifest\.py", _normalize_path(line))
        if match:
            apps.add(match.group(1))
    return apps


def _read_required_child_text(root: Path, path: Path) -> str:
    resolved = _resolve_child_path(root, path, label="version file")
    if not resolved.is_file():
        raise SystemExit(f"Version file is not a regular file: {resolved}")
    value = resolved.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit(f"Version file is empty: {resolved}")
    return value


def _resolve_child_path(root: Path, path: Path, *, label: str) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise SystemExit(f"{label.capitalize()} escapes repository root: {resolved}")
    return resolved


def _git_stdout(cmd: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _parse_optional_version(value: str) -> Version | None:
    if not value:
        return None
    try:
        return Version.parse(value)
    except ValueError:
        return None


def _is_parseable_version(value: str) -> bool:
    try:
        Version.parse(value)
    except ValueError:
        return False
    return True


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _github_output_delimiter(markdown: str) -> str:
    markdown_lines = set(markdown.splitlines())
    while True:
        delimiter = f"ghadelim_{secrets.token_hex(16)}"
        if delimiter not in markdown_lines:
            return delimiter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan the next reviewed release VERSION.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--package-name", default=DEFAULT_PACKAGE_NAME)
    parser.add_argument("--version-file", default="VERSION")
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument(
        "--bump-level",
        choices=["auto", "patch", "minor", "major"],
        default="auto",
        help="Override automatic maturity detection.",
    )
    parser.add_argument("--published-versions-json", default="")
    parser.add_argument("--skip-pypi", action="store_true")
    parser.add_argument("--pypi-timeout", type=float, default=15.0)
    parser.add_argument("--write-version", action="store_true")
    parser.add_argument("--github-output")
    parser.add_argument("--summary-file")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _parse_published_versions(raw_value: str) -> list[str]:
    if not raw_value.strip():
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Published versions JSON is invalid: {exc}") from exc
    if not isinstance(payload, list):
        raise SystemExit("Published versions JSON must be a list.")
    return [str(item) for item in payload]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    plan = plan_release_version(
        root=root,
        package_name=args.package_name,
        version_file=Path(args.version_file),
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        bump_level=args.bump_level,
        published_versions=_parse_published_versions(args.published_versions_json),
        pypi_timeout=float(args.pypi_timeout),
        skip_pypi=bool(args.skip_pypi),
    )
    if args.write_version and plan.version_bumped:
        write_version_file(root=root, version_file=Path(args.version_file), version=plan.next_version)
    emit_plan(
        plan,
        github_output=Path(args.github_output) if args.github_output else None,
        summary_file=Path(args.summary_file) if args.summary_file else None,
        json_output=bool(args.json_output),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
