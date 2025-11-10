from __future__ import annotations

"""Utilities for building and parsing the project changelog."""

from dataclasses import dataclass
import re
import subprocess
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class Commit:
    """A simplified representation of a git commit."""

    sha: str
    date: str
    subject: str


@dataclass
class ChangelogSection:
    """A rendered changelog section."""

    title: str
    entries: List[str]
    version: Optional[str] = None
    date: Optional[str] = None


_RE_RELEASE = re.compile(
    r"^(?:pre-release commit|Release)\s+v?(?P<version>[0-9A-Za-z][0-9A-Za-z.\-_]*)",
    re.IGNORECASE,
)
_RE_TITLE_VERSION = re.compile(r"^v(?P<version>[0-9A-Za-z][0-9A-Za-z.\-_]*)")
_RE_TITLE_DATE = re.compile(r"\((?P<date>\d{4}-\d{2}-\d{2})\)")


def _read_commits(range_spec: str) -> List[Commit]:
    """Return commits for *range_spec* ordered newest first."""

    cmd = [
        "git",
        "log",
        range_spec,
        "--no-merges",
        "--date=short",
        "--pretty=format:%H%x00%ad%x00%s",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    commits: list[Commit] = []
    for raw in proc.stdout.splitlines():
        parts = raw.split("\x00")
        if len(parts) != 3:
            continue
        sha, date, subject = parts
        commits.append(Commit(sha=sha, date=date, subject=subject))
    return commits


def _extract_release_version(subject: str) -> Optional[str]:
    match = _RE_RELEASE.match(subject)
    if match:
        return match.group("version")
    return None


def _should_include_subject(subject: str) -> bool:
    return len(subject.split()) > 3


def _format_title(version: str, date: Optional[str]) -> str:
    if date:
        return f"v{version} ({date})"
    return f"v{version}"


def _sections_from_commits(commits: Iterable[Commit]) -> List[ChangelogSection]:
    unreleased: list[str] = []
    releases: list[ChangelogSection] = []
    release_map: dict[str, ChangelogSection] = {}
    current_release: ChangelogSection | None = None

    for commit in commits:
        version = _extract_release_version(commit.subject)
        if version:
            section = release_map.get(version)
            if section is None:
                section = ChangelogSection(
                    title=_format_title(version, commit.date),
                    entries=[],
                    version=version,
                    date=commit.date,
                )
                releases.append(section)
                release_map[version] = section
            else:
                if commit.date and not section.date:
                    section.date = commit.date
                    section.title = _format_title(version, commit.date)
            current_release = section
            continue
        if not _should_include_subject(commit.subject):
            continue
        entry = f"- {commit.sha[:8]} {commit.subject}"
        if current_release is None:
            if entry not in unreleased:
                unreleased.append(entry)
        else:
            if entry not in current_release.entries:
                current_release.entries.append(entry)

    sections: list[ChangelogSection] = [
        ChangelogSection(title="Unreleased", entries=unreleased, version=None, date=None)
    ]
    sections.extend(releases)
    return sections


def _parse_sections(text: str) -> List[ChangelogSection]:
    lines = text.splitlines()
    sections: list[ChangelogSection] = []
    i = 0
    total = len(lines)
    while i < total:
        title = lines[i]
        underline_index = i + 1
        if underline_index >= total:
            break
        underline = lines[underline_index]
        if set(underline) == {"-"} and len(underline) == len(title):
            entries: list[str] = []
            i = underline_index + 1
            # Skip single blank line immediately after the heading if present.
            if i < total and lines[i] == "":
                i += 1
            while i < total and lines[i] != "":
                entries.append(lines[i])
                i += 1
            version = None
            date = None
            match_version = _RE_TITLE_VERSION.match(title)
            if match_version:
                version = match_version.group("version")
                match_date = _RE_TITLE_DATE.search(title)
                if match_date:
                    date = match_date.group("date")
            sections.append(
                ChangelogSection(title=title, entries=entries, version=version, date=date)
            )
            while i < total and lines[i] == "":
                i += 1
            continue
        i += 1
    return sections


def _latest_release_version(previous_text: str) -> Optional[str]:
    for section in _parse_sections(previous_text):
        if section.version:
            return section.version
    return None


def _find_release_commit(version: str) -> Optional[str]:
    normalized = version.lstrip("v")
    search_terms = [
        f"Release v{normalized}",
        f"Release {normalized}",
        f"pre-release commit v{normalized}",
        f"pre-release commit {normalized}",
    ]
    for term in search_terms:
        proc = subprocess.run(
            [
                "git",
                "log",
                "--max-count=1",
                "--format=%H",
                "--fixed-strings",
                f"--grep={term}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        sha = proc.stdout.strip()
        if sha:
            return sha.splitlines()[0]
    return None


def _resolve_release_commit_from_text(previous_text: str) -> Optional[str]:
    version = _latest_release_version(previous_text)
    if not version:
        return None
    return _find_release_commit(version)


def _merge_sections(
    new_sections: Iterable[ChangelogSection],
    old_sections: Iterable[ChangelogSection],
    *,
    reopen_latest: bool = False,
) -> List[ChangelogSection]:
    merged = list(new_sections)
    old_sections_list = list(old_sections)
    version_to_section: dict[str, ChangelogSection] = {}
    unreleased_section: ChangelogSection | None = None

    for section in merged:
        if section.version is None and unreleased_section is None:
            unreleased_section = section
        if section.version:
            version_to_section[section.version] = section

    first_release_version: str | None = None
    for old in old_sections_list:
        if old.version:
            first_release_version = old.version
            break

    reopened_latest_version = False

    for old in old_sections_list:
        if old.version is None:
            if unreleased_section is None:
                unreleased_section = ChangelogSection(
                    title=old.title,
                    entries=list(old.entries),
                    version=None,
                    date=None,
                )
                merged.insert(0, unreleased_section)
            else:
                # Preserve the freshly generated ``Unreleased`` entries instead of
                # merging in stale content from the previous changelog text.
                # The older implementation discarded the previous ``Unreleased``
                # notes entirely, so keep that behaviour to avoid resurrecting
                # entries that were already promoted to a tagged release.
                continue
            continue

        existing = version_to_section.get(old.version)
        if existing is None:
            if (
                reopen_latest
                and first_release_version
                and old.version == first_release_version
                and not reopened_latest_version
                and unreleased_section is not None
            ):
                for entry in old.entries:
                    if entry not in unreleased_section.entries:
                        unreleased_section.entries.append(entry)
                reopened_latest_version = True
                continue
            copied = ChangelogSection(
                title=old.title,
                entries=list(old.entries),
                version=old.version,
                date=old.date,
            )
            merged.append(copied)
            version_to_section[old.version] = copied
            continue

        if old.date and not existing.date:
            existing.date = old.date
            existing.title = _format_title(old.version, old.date)
        for entry in old.entries:
            if entry not in existing.entries:
                existing.entries.append(entry)

    return merged


def _resolve_start_tag(explicit: str | None = None) -> Optional[str]:
    """Return the most recent tag that should seed the changelog range."""

    if explicit:
        return explicit

    exact = subprocess.run(
        ["git", "describe", "--tags", "--exact-match", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if exact.returncode == 0:
        has_parent = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^"],
            capture_output=True,
            text=True,
            check=False,
        )
        if has_parent.returncode == 0:
            previous = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0", "HEAD^"],
                capture_output=True,
                text=True,
                check=False,
            )
            if previous.returncode == 0:
                tag = previous.stdout.strip()
                if tag:
                    return tag
        return None

    describe = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True,
        text=True,
        check=False,
    )
    if describe.returncode == 0:
        tag = describe.stdout.strip()
        if tag:
            return tag
    return None


def determine_range_spec(
    start_tag: str | None = None, *, previous_text: str | None = None
) -> str:
    """Return the git range specification to build the changelog."""

    resolved = _resolve_start_tag(start_tag)
    if resolved:
        return f"{resolved}..HEAD"

    if previous_text:
        release_commit = _resolve_release_commit_from_text(previous_text)
        if release_commit:
            return f"{release_commit}..HEAD"

    return "HEAD"


def collect_sections(
    *,
    range_spec: str = "HEAD",
    previous_text: str | None = None,
    reopen_latest: bool = False,
) -> List[ChangelogSection]:
    """Return changelog sections for *range_spec*.

    When ``previous_text`` is provided, sections not regenerated in the current run
    are appended so long as they can be parsed from the existing changelog. Set
    ``reopen_latest`` to ``True`` when the caller intends to move the most recent
    release notes back into the ``Unreleased`` section (for example, when
    preparing a release retry before a new tag is created).
    """

    commits = _read_commits(range_spec)
    sections = _sections_from_commits(commits)
    if previous_text:
        old_sections = _parse_sections(previous_text)
        sections = _merge_sections(
            sections, old_sections, reopen_latest=reopen_latest
        )
    return sections


def render_changelog(sections: Iterable[ChangelogSection]) -> str:
    lines: list[str] = ["Changelog", "=========", ""]
    for section in sections:
        lines.append(section.title)
        lines.append("-" * len(section.title))
        lines.append("")
        lines.extend(section.entries)
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)


def latest_release_version_from_history(range_spec: str = "HEAD") -> Optional[str]:
    """Return the most recent release version present in git history."""

    sections = collect_sections(range_spec=range_spec)
    for section in sections:
        if section.version:
            return section.version.lstrip("v")
    return None


def changelog_has_release_section(text: str, version: str) -> bool:
    """Return ``True`` when the changelog text contains a section for *version*."""

    normalized = version.lstrip("v")
    for section in _parse_sections(text):
        if section.version and section.version.lstrip("v") == normalized:
            return True
    return False


def extract_release_notes(text: str, version: str) -> str:
    """Return the changelog entries matching *version*.

    When no dedicated section for the release exists, the ``Unreleased`` section is
    returned instead to capture the pending notes for the current release.
    """

    sections = _parse_sections(text)
    normalized = version.lstrip("v")
    for section in sections:
        if section.version and section.version.lstrip("v") == normalized:
            return "\n".join(section.entries).strip()
    for section in sections:
        if section.version is None:
            return "\n".join(section.entries).strip()
    return ""


__all__ = [
    "ChangelogSection",
    "Commit",
    "determine_range_spec",
    "collect_sections",
    "changelog_has_release_section",
    "latest_release_version_from_history",
    "extract_release_notes",
    "render_changelog",
]
