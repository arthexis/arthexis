from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import subprocess
from typing import Iterable, Sequence

from django.conf import settings
from django.core.cache import cache
from django.utils.text import slugify
from django.utils.translation import gettext as _

from utils import revision

logger = logging.getLogger(__name__)


class ChangelogError(RuntimeError):
    """Raised when changelog data cannot be generated."""


@dataclass(frozen=True)
class ChangelogCommit:
    """Single commit entry within a changelog section."""

    sha: str
    summary: str
    author: str
    authored_at: datetime
    commit_url: str | None = None

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


@dataclass(frozen=True)
class ChangelogSection:
    """Grouped commits for a release or the unreleased segment."""

    slug: str
    title: str
    commits: tuple[ChangelogCommit, ...]
    is_unreleased: bool = False
    released_on: datetime | None = None
    version: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.commits


@dataclass(frozen=True)
class ChangelogPage:
    sections: tuple[ChangelogSection, ...]
    next_page: int | None
    has_more: bool


@dataclass(frozen=True)
class _ReleaseMarker:
    sha: str
    version: str
    committed_at: datetime


_CACHE_TIMEOUT_SECONDS = 120
_CACHE_KEY_PREFIX = "apps.core.changelog.sections"
_INITIAL_SECTION_COUNT = 2


def get_initial_page(initial_count: int = _INITIAL_SECTION_COUNT) -> ChangelogPage:
    sections = _load_sections()
    if not sections:
        return ChangelogPage(tuple(), None, False)

    count = max(0, initial_count)
    initial_sections = tuple(sections[:count])
    remaining = len(sections) - len(initial_sections)
    has_more = remaining > 0
    next_page = 1 if has_more else None
    return ChangelogPage(initial_sections, next_page, has_more)


def get_page(page: int, per_page: int, *, offset: int = _INITIAL_SECTION_COUNT) -> ChangelogPage:
    if page < 1:
        raise ChangelogError("Invalid page index.")
    if per_page < 1:
        raise ChangelogError("Invalid page size.")

    sections = _load_sections()
    if not sections:
        return ChangelogPage(tuple(), None, False)

    start = max(0, offset) + (page - 1) * per_page
    end = start + per_page
    chunk = tuple(sections[start:end])
    has_more = end < len(sections)
    next_page = page + 1 if has_more else None
    return ChangelogPage(chunk, next_page, has_more)


def get_latest_commits(
    limit: int = 3,
    *,
    exclude_prefixes: Sequence[str] = ("chore", "mm"),
) -> tuple[ChangelogCommit, ...]:
    if limit < 1:
        return tuple()

    cache_key = _latest_commits_cache_key(limit, exclude_prefixes)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    commits: list[ChangelogCommit] = []
    batch_size = max(limit * 4, 10)
    skip = 0
    for _ in range(5):
        batch = list(_gather_commits("HEAD", max_count=batch_size, skip=skip))
        if not batch:
            break
        for commit in batch:
            if _summary_is_excluded(commit.summary, exclude_prefixes):
                continue
            commits.append(commit)
            if len(commits) >= limit:
                break
        if len(commits) >= limit or len(batch) < batch_size:
            break
        skip += len(batch)

    latest_commits = tuple(commits[:limit])
    cache.set(cache_key, latest_commits, timeout=_CACHE_TIMEOUT_SECONDS)
    return latest_commits


def _load_sections() -> tuple[ChangelogSection, ...]:
    cache_key = _cache_key()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        sections = tuple(_build_sections())
    except ChangelogError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected error while building changelog sections: %s", exc)
        raise ChangelogError("Unable to build the changelog report.") from exc

    cache.set(cache_key, sections, timeout=_CACHE_TIMEOUT_SECONDS)
    return sections


def _build_sections() -> Iterable[ChangelogSection]:
    markers = list(_gather_release_markers())
    if markers:
        latest = markers[0]
        unreleased_commits = tuple(_gather_commits(f"{latest.sha}..HEAD"))
    else:
        unreleased_commits = tuple(_gather_commits("HEAD"))

    yield ChangelogSection(
        slug="unreleased",
        title=_("Unreleased"),
        commits=unreleased_commits,
        is_unreleased=True,
    )

    for index, marker in enumerate(markers):
        previous_sha = markers[index + 1].sha if index + 1 < len(markers) else None
        if previous_sha:
            range_spec = f"{previous_sha}..{marker.sha}"
        else:
            range_spec = marker.sha

        commits = tuple(_gather_commits(range_spec))
        commits = tuple(commit for commit in commits if commit.sha != marker.sha)

        yield ChangelogSection(
            slug=slugify(marker.version or marker.sha) or marker.sha[:12],
            title=_("Version %(version)s") % {"version": marker.version},
            commits=commits,
            is_unreleased=False,
            released_on=marker.committed_at,
            version=marker.version,
        )


def _gather_release_markers() -> Iterable[_ReleaseMarker]:
    output = _run_git(["log", "--format=%H%x1f%ct%x1f%s%x1e", "VERSION"])
    markers: list[_ReleaseMarker] = []
    for chunk in output.strip().split("\x1e"):
        if not chunk:
            continue
        sha, timestamp, _message = (chunk.split("\x1f") + [""])[:3]
        version = _resolve_version_for_commit(sha)
        if not version:
            continue
        try:
            committed_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except ValueError:
            committed_at = datetime.now(tz=timezone.utc)
        markers.append(_ReleaseMarker(sha=sha, version=version, committed_at=committed_at))
    return markers


def _resolve_version_for_commit(sha: str) -> str | None:
    try:
        version_text = _run_git(["show", f"{sha}:VERSION"])
    except ChangelogError:
        return None
    version = version_text.strip()
    return version or None


def _gather_commits(
    range_spec: str,
    *,
    max_count: int | None = None,
    skip: int = 0,
) -> Iterable[ChangelogCommit]:
    try:
        log_args = [
            "log",
            "--no-merges",
            "--format=%H%x1f%ct%x1f%an%x1f%s%x1e",
        ]
        if max_count:
            log_args.extend(["--max-count", str(max_count)])
        if skip:
            log_args.extend(["--skip", str(skip)])
        output = _run_git(
            [
                *log_args,
                range_spec,
            ]
        )
    except ChangelogError:
        return tuple()

    from apps.core.system import _github_commit_url  # Local import to avoid circular dependency

    for chunk in output.strip().split("\x1e"):
        if not chunk:
            continue
        sha, timestamp, author, summary = (chunk.split("\x1f") + ["", "", ""])[:4]
        if not sha:
            continue
        try:
            authored_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except ValueError:
            authored_at = datetime.now(tz=timezone.utc)
        commit_url = _github_commit_url(sha)
        yield ChangelogCommit(
            sha=sha,
            summary=summary.strip() or "(no commit message)",
            author=author.strip() or "",
            authored_at=authored_at,
            commit_url=commit_url or None,
        )


def _run_git(args: Sequence[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=settings.BASE_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on environment
        logger.warning("Git executable unavailable: %s", exc)
        raise ChangelogError("Git is not available on this system.") from exc
    except subprocess.CalledProcessError as exc:
        logger.debug("Git command failed: %s", exc)
        raise ChangelogError("Unable to read repository history.") from exc


def _cache_key() -> str:
    revision_hash = revision.get_revision() or "unknown"
    return f"{_CACHE_KEY_PREFIX}:{revision_hash}"


def _latest_commits_cache_key(limit: int, prefixes: Sequence[str]) -> str:
    prefix_key = ",".join(prefix.strip().lower() for prefix in prefixes if prefix.strip())
    return f"{_cache_key()}:latest:{limit}:{prefix_key}"


def _summary_is_excluded(summary: str, prefixes: Sequence[str]) -> bool:
    normalized = summary.strip().lower()
    if not normalized:
        return False
    for prefix in prefixes:
        key = prefix.strip().lower()
        if key and (normalized == key or normalized.startswith((f"{key}:", f"{key}(", f"{key} "))):
            return True
    return False
