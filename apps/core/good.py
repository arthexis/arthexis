"""Operational readiness helpers for the ``good`` management command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
from typing import Iterable

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.counters.models import DashboardRule
from apps.features.models import Feature
from apps.nodes.feature_checks import feature_checks
from apps.nodes.models import Node, NodeFeature
from apps.tests.models import TestResult


SEVERITY_RANK = {
    "critical": 0,
    "important": 1,
    "warning": 2,
    "minor": 3,
}
ERROR_KEYWORDS = (" trace", "exception", " error", "fatal", "critical")
MINIMUM_DISK_FREE_GB = 2
MINIMUM_MEMORY_GB = 2
LOG_LOOKBACK_DAYS = 7
DEFAULT_CONNECTIVITY_ENDPOINTS = (("one.one.one.one", 443), ("dns.google", 443))


@dataclass(frozen=True)
class GoodIssue:
    """Represent one readiness issue reported by the ``good`` command.

    Args:
        key: Stable machine-friendly identifier for the issue.
        title: Human-friendly issue title.
        detail: Specific operator guidance or evidence.
        severity: Priority bucket used for ranking output.
        category: Functional area the issue belongs to.
    """

    key: str
    title: str
    detail: str
    severity: str
    category: str

    @property
    def is_minor(self) -> bool:
        """Return whether this issue is informational rather than blocking."""

        return self.severity == "minor"


@dataclass(frozen=True)
class GoodReport:
    """Bundle the overall readiness result for the ``good`` command.

    Args:
        issues: Sorted list of issues worth showing to operators.
        tagline: Marketing-ready default success line.
    """

    issues: tuple[GoodIssue, ...]
    tagline: str = "Arthexis is Good"

    @property
    def has_issues(self) -> bool:
        """Return whether any issue was detected."""

        return bool(self.issues)

    @property
    def has_non_minor_issues(self) -> bool:
        """Return whether any issue should appear in default command output."""

        return any(not issue.is_minor for issue in self.issues)

    @property
    def success_line(self) -> str:
        """Return the one-line success output requested by product guidance.

        Returns:
            The plain tagline when no issues exist, or the starred tagline when
            only minor considerations were found.

        Raises:
            ValueError: If important issues exist and a success line should not
                be shown.
        """

        if not self.issues:
            return self.tagline
        if self.has_non_minor_issues:
            raise ValueError("Cannot render a success line when non-minor issues exist.")
        return f"{self.tagline}*"


def build_good_report() -> GoodReport:
    """Collect and rank readiness issues for the local Arthexis deployment.

    Returns:
        A :class:`GoodReport` containing the ranked issues to consider.
    """

    issues = sorted(_iter_issues(), key=_issue_sort_key)
    return GoodReport(issues=tuple(issues))


def _iter_issues() -> Iterable[GoodIssue]:
    """Yield readiness issues discovered across operational checks."""

    yield from _check_recent_test_results()
    yield from _check_instance_availability()
    yield from _check_internet_connectivity()
    yield from _check_recent_logs()
    yield from _check_recent_journal_errors()
    yield from _check_suite_feature_eligibility()
    yield from _check_node_feature_eligibility()
    yield from _check_hardware_requirements()
    yield from _check_platform_compatibility()
    yield from _check_dashboard_rules()


def _issue_sort_key(issue: GoodIssue) -> tuple[int, str, str]:
    """Return the sort key that ranks issues by severity, category, and title."""

    return (SEVERITY_RANK.get(issue.severity, 99), issue.category, issue.title)


def _check_recent_test_results() -> Iterable[GoodIssue]:
    """Inspect recent stored test results for failures or missing coverage."""

    cutoff = timezone.now() - timedelta(days=LOG_LOOKBACK_DAYS)
    try:
        recent_results = list(TestResult.objects.filter(created_at__gte=cutoff))
    except (OperationalError, ProgrammingError):
        yield GoodIssue(
            key="tests-unavailable",
            title="Recent test status could not be read",
            detail="TestResult rows are unavailable; run migrations and `manage.py test run -- ...`.",
            severity="warning",
            category="tests",
        )
        return

    if not recent_results:
        yield GoodIssue(
            key="tests-missing",
            title="No recent test results recorded",
            detail="Run `manage.py test run -- ...` to capture a fresh suite result for this setup.",
            severity="minor",
            category="tests",
        )
        return

    failed = [
        result for result in recent_results if result.status in {TestResult.Status.ERROR, TestResult.Status.FAILED}
    ]
    if failed:
        latest = max(failed, key=lambda item: item.created_at)
        yield GoodIssue(
            key="tests-failing",
            title="Recent tests include failures",
            detail=(
                f"{len(failed)} failing/error result(s) were recorded since {cutoff.date()}; "
                f"latest: {latest.node_id} [{latest.status}] at {latest.created_at.isoformat()}."
            ),
            severity="critical",
            category="tests",
        )


def _check_instance_availability() -> Iterable[GoodIssue]:
    """Check whether the local suite appears registered and reachable."""

    try:
        node = Node.get_local()
    except (OperationalError, ProgrammingError):
        node = None

    if node is None:
        yield GoodIssue(
            key="local-node-missing",
            title="Local node registration is missing",
            detail="Register the local node so Arthexis can evaluate site, role, and feature readiness.",
            severity="important",
            category="availability",
        )
        return

    candidates = list(_iter_instance_connection_targets(node))
    if any(_can_connect(host, candidate_port) for _, host, candidate_port in candidates):
        return

    target = ", ".join(f"{scheme}://{host}:{candidate_port}" for scheme, host, candidate_port in candidates)
    yield GoodIssue(
        key="instance-unreachable",
        title="Local instance did not accept a TCP connection",
        detail=f"Checked {target} and could not establish a connection.",
        severity="important",
        category="availability",
    )


def _iter_instance_connection_targets(node: Node) -> Iterable[tuple[str, str, int]]:
    """Yield unique TCP connection targets that may reach the local node.

    Args:
        node: The local node registration being checked.

    Returns:
        An iterable of ``(scheme, host, port)`` tuples ordered by preference.
    """

    port = node.get_preferred_port()
    base_domain = node.get_base_domain()
    seen: set[tuple[str, str, int]] = set()
    for host in node.get_remote_host_candidates(resolve_dns=False):
        if not host:
            continue
        targets = [("http", host, port)]
        if host == base_domain:
            targets.insert(0, ("https", host, 443))
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            yield target

    if not seen:
        yield ("http", "127.0.0.1", port)


def _check_internet_connectivity() -> Iterable[GoodIssue]:
    """Verify basic outbound internet connectivity for integrations and updates."""

    endpoints = tuple(getattr(settings, "GOOD_CONNECTIVITY_ENDPOINTS", DEFAULT_CONNECTIVITY_ENDPOINTS))
    if any(_can_connect(host, port, timeout=2.0) for host, port in endpoints):
        return

    endpoint_text = ", ".join(f"{host}:{port}" for host, port in endpoints)
    yield GoodIssue(
        key="internet-offline",
        title="Outbound internet connectivity appears unavailable",
        detail=f"Tried TCP connectivity to {endpoint_text} without success.",
        severity="important",
        category="network",
    )


def _check_recent_logs() -> Iterable[GoodIssue]:
    """Look for suspicious recent errors in filesystem logs."""

    log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    if not log_dir.exists():
        yield GoodIssue(
            key="log-dir-missing",
            title="Log directory is missing",
            detail=f"Expected log directory at {log_dir}.",
            severity="minor",
            category="logs",
        )
        return

    cutoff_ts = (timezone.now() - timedelta(days=LOG_LOOKBACK_DAYS)).timestamp()
    suspicious = 0
    example_path = ""
    log_paths = sorted(log_dir.glob("*.log"), key=_log_sort_mtime, reverse=True)
    for path in log_paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff_ts:
            continue
        try:
            with path.open(encoding="utf-8", errors="ignore") as handle:
                line_count = sum(1 for line in handle if _is_suspicious_log_line(line))
        except OSError:
            continue
        if line_count:
            suspicious += line_count
            if not example_path:
                example_path = str(path)

    if suspicious:
        yield GoodIssue(
            key="recent-log-errors",
            title="Recent filesystem logs contain error-like entries",
            detail=(
                f"Found {suspicious} suspicious log line(s) within {LOG_LOOKBACK_DAYS} days; "
                f"example recent file: {example_path}."
            ),
            severity="important",
            category="logs",
        )


def _check_recent_journal_errors() -> Iterable[GoodIssue]:
    """Inspect recent system journal errors when journalctl is available."""

    journalctl = shutil.which("journalctl")
    if not journalctl:
        yield GoodIssue(
            key="journalctl-missing",
            title="System journal is unavailable from this environment",
            detail="`journalctl` was not found; log review will rely on filesystem logs only.",
            severity="minor",
            category="logs",
        )
        return

    command = [journalctl, "--since", f"{LOG_LOOKBACK_DAYS} days ago", "-p", "err..alert", "--no-pager"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in {0, 1}:
        yield GoodIssue(
            key="journalctl-failed",
            title="System journal could not be queried",
            detail=(result.stderr or result.stdout or "journalctl failed").strip(),
            severity="warning",
            category="logs",
        )
        return

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if lines:
        yield GoodIssue(
            key="journal-errors",
            title="Recent journal errors were reported",
            detail=f"`journalctl` returned {len(lines)} non-empty error line(s) from the last {LOG_LOOKBACK_DAYS} days.",
            severity="important",
            category="logs",
        )


def _check_suite_feature_eligibility() -> Iterable[GoodIssue]:
    """Report optional suite features that are disabled or blocked on this node."""

    try:
        node = Node.get_local()
        features = list(Feature.objects.select_related("node_feature").order_by("display"))
    except (OperationalError, ProgrammingError):
        return

    for feature in features:
        if feature.is_enabled_for_node(node=node):
            continue
        if not feature.is_enabled:
            yield GoodIssue(
                key=f"suite-feature-disabled:{feature.slug}",
                title=f"Optional suite feature disabled: {feature.display}",
                detail="The feature toggle is off and can be enabled when this deployment needs it.",
                severity="minor",
                category="features",
            )
            continue
        if feature.node_feature_id:
            node_feature = getattr(feature, "node_feature", None)
            node_feature_slug = getattr(node_feature, "slug", str(feature.node_feature_id))
            yield GoodIssue(
                key=f"suite-feature-blocked:{feature.slug}",
                title=f"Suite feature waiting on node capability: {feature.display}",
                detail=f"Requires node feature `{node_feature_slug}` before it can activate on this node.",
                severity="minor",
                category="features",
            )


def _check_node_feature_eligibility() -> Iterable[GoodIssue]:
    """Run registered node feature checks to expose optional activation gaps."""

    try:
        node = Node.get_local()
        features = list(NodeFeature.objects.order_by("display"))
    except (OperationalError, ProgrammingError):
        return

    for feature in features:
        try:
            result = feature_checks.run(feature, node=node)
        except Exception as exc:  # pragma: no cover - defensive against optional integrations
            yield GoodIssue(
                key=f"node-feature-check-failed:{feature.slug}",
                title=f"Node feature check failed: {feature.display}",
                detail=f"Optional checker raised {exc.__class__.__name__}: {exc}",
                severity="warning",
                category="features",
            )
            continue
        if result is None or result.success:
            continue
        yield GoodIssue(
            key=f"node-feature:{feature.slug}",
            title=f"Node feature consideration: {feature.display}",
            detail=result.message,
            severity="minor",
            category="features",
        )


def _check_hardware_requirements() -> Iterable[GoodIssue]:
    """Check coarse hardware requirements for a healthy local node."""

    disk = shutil.disk_usage(settings.BASE_DIR)
    free_gb = disk.free / (1024**3)
    if free_gb < MINIMUM_DISK_FREE_GB:
        yield GoodIssue(
            key="disk-low",
            title="Available disk space is low",
            detail=f"Free disk space is {free_gb:.1f} GiB; keep at least {MINIMUM_DISK_FREE_GB} GiB available.",
            severity="important",
            category="hardware",
        )

    memory_gb = _memory_gib()
    if memory_gb is not None and memory_gb < MINIMUM_MEMORY_GB:
        yield GoodIssue(
            key="memory-low",
            title="System memory is below the recommended floor",
            detail=f"Detected about {memory_gb:.1f} GiB RAM; recommended minimum is {MINIMUM_MEMORY_GB} GiB.",
            severity="warning",
            category="hardware",
        )

    cpu_count = os.cpu_count() or 0
    if 0 < cpu_count < 2:
        yield GoodIssue(
            key="cpu-low",
            title="CPU capacity is minimal",
            detail=f"Detected {cpu_count} CPU core(s); background jobs and integrations benefit from at least 2.",
            severity="minor",
            category="hardware",
        )


def _check_platform_compatibility() -> Iterable[GoodIssue]:
    """Report obvious platform mismatches for the expected Arthexis runtime."""

    system_name = platform.system().lower()
    if system_name != "linux":
        yield GoodIssue(
            key="platform-non-linux",
            title="Platform compatibility may be limited",
            detail=f"Detected {platform.system()}; the suite is typically operated on Linux hosts with system services.",
            severity="warning",
            category="platform",
        )

    service_mode = getattr(settings, "ARTHEXIS_SERVICE_MODE", "").strip().lower()
    if not service_mode:
        from apps.core.system.filesystem import _read_service_mode

        service_mode = _read_service_mode(Path(settings.BASE_DIR) / "var" / "lock")

    if service_mode != "embedded" and not shutil.which("systemctl"):
        yield GoodIssue(
            key="systemctl-missing",
            title="systemd tooling is unavailable",
            detail="`systemctl` is missing, so service-oriented suite features may not be fully operable here.",
            severity="minor",
            category="platform",
        )


def _check_dashboard_rules() -> Iterable[GoodIssue]:
    """Evaluate dashboard rules and surface any failing operator checks."""

    try:
        rules = list(DashboardRule.objects.select_related("content_type").order_by("name"))
    except (OperationalError, ProgrammingError):
        return

    for rule in rules:
        result = rule.evaluate()
        if result.get("success"):
            continue
        message = str(result.get("message") or "Rule failed.")
        yield GoodIssue(
            key=f"dashboard-rule:{rule.name}",
            title=f"Dashboard rule failed: {rule.name}",
            detail=message,
            severity="warning",
            category="dashboard",
        )


def marketing_tagline(*, docs_url: str | None = None) -> str:
    """Return the recommended slogan/tagline for the new command.

    Args:
        docs_url: Optional documentation URL to append to the marketing line.

    Returns:
        A concise tagline that can be reused by docs or marketing surfaces.
    """

    base = "Arthexis is Good[*] — one command to prove your suite is ready."
    if not docs_url:
        return base
    return f"{base} Learn more: {docs_url}"


def docs_url() -> str:
    """Return the internal documentation URL for the ``good`` command when available."""

    try:
        return reverse("docs:docs-document", args=["operations/good-command"])
    except NoReverseMatch:
        return "/docs/operations/good-command/"


def _can_connect(host: str, port: int, *, timeout: float = 1.5) -> bool:
    """Return whether a TCP connection to ``host:port`` succeeds within ``timeout``."""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_suspicious_log_line(line: str) -> bool:
    """Return whether a log line looks like an actionable error entry."""

    text = line.strip().lower()
    if not text:
        return False
    return any(keyword in f" {text}" for keyword in ERROR_KEYWORDS)


def _log_sort_mtime(path: Path) -> float:
    """Return a best-effort log-file mtime used for recent-file ordering.

    Args:
        path: Log file path being considered.

    Returns:
        The file modification timestamp, or ``0.0`` when it cannot be read.
    """

    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _memory_gib() -> float | None:
    """Return detected system memory in GiB when procfs information is available."""

    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if not line.startswith("MemTotal:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                return None
            return int(parts[1]) / (1024**2)
    except (OSError, ValueError):
        return None
    return None
