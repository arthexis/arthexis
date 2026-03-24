"""Tests for the ``good`` management command and readiness helpers."""

from __future__ import annotations

from types import SimpleNamespace


def test_node_feature_checks_report_failures_without_aborting(monkeypatch) -> None:
    """Optional feature checker exceptions should degrade into a warning issue."""

    feature = SimpleNamespace(slug="llm-summary", display="LLM Summary")
    monkeypatch.setattr("apps.core.good.Node.get_local", lambda: object())
    monkeypatch.setattr("apps.core.good.NodeFeature.objects.order_by", lambda *args, **kwargs: [feature])

    def raise_runtime_error(*args, **kwargs):
        raise RuntimeError("summary app is not migrated")

    monkeypatch.setattr("apps.core.good.feature_checks.run", raise_runtime_error)

    from apps.core.good import _check_node_feature_eligibility

    issues = list(_check_node_feature_eligibility())

    assert len(issues) == 1
    assert issues[0].key == "node-feature-check-failed:llm-summary"
    assert issues[0].severity == "warning"
    assert "RuntimeError" in issues[0].detail


def test_platform_check_skips_systemctl_warning_in_embedded_mode(settings, monkeypatch) -> None:
    """Embedded installs should not be marked down just because systemctl is absent."""

    settings.ARTHEXIS_SERVICE_MODE = "embedded"
    monkeypatch.setattr("apps.core.good.shutil.which", lambda name: None)

    from apps.core.good import _check_platform_compatibility

    issues = list(_check_platform_compatibility())

    assert not any(issue.key == "systemctl-missing" for issue in issues)
