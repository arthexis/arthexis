"""Tests for the ``good`` management command and readiness helpers."""

from __future__ import annotations

from io import StringIO

import pytest

from django.core.management import call_command

from apps.core.good import GoodIssue, GoodReport, _issue_sort_key, marketing_tagline


def test_marketing_tagline_can_include_docs_url() -> None:
    """The tagline helper should support a docs link for marketing surfaces."""

    result = marketing_tagline(docs_url="/docs/operations/good-command/")

    assert "Arthexis is Good[*]" in result
    assert "/docs/operations/good-command/" in result


def test_issue_sort_key_orders_by_priority() -> None:
    """Critical issues should rank ahead of warnings and minor considerations."""

    issues = [
        GoodIssue("a", "Minor", "detail", "minor", "features"),
        GoodIssue("b", "Critical", "detail", "critical", "tests"),
        GoodIssue("c", "Warning", "detail", "warning", "logs"),
    ]

    ordered = sorted(issues, key=_issue_sort_key)

    assert [item.key for item in ordered] == ["b", "c", "a"]


def test_success_line_rejects_non_minor_issue_reports() -> None:
    """Reports with blocking issues should not expose a misleading success line."""

    report = GoodReport(
        issues=(
            GoodIssue("critical", "Tests failed", "detail", "critical", "tests"),
        )
    )

    with pytest.raises(ValueError, match="non-minor issues"):
        _ = report.success_line


def test_good_command_tagline_includes_docs_link(monkeypatch) -> None:
    """The dedicated tagline mode should point marketing to the command docs."""

    monkeypatch.setattr("apps.core.management.commands.good.docs_url", lambda: "/docs/operations/good-command/")

    output = StringIO()
    call_command("good", "--tagline", stdout=output)

    rendered = output.getvalue().strip()
    assert "Arthexis is Good[*]" in rendered
    assert "/docs/operations/good-command/" in rendered


def test_good_command_prints_plain_success_when_no_issues(monkeypatch) -> None:
    """The command should print only the slogan when everything looks good."""

    monkeypatch.setattr(
        "apps.core.management.commands.good.build_good_report",
        lambda: GoodReport(issues=()),
    )

    output = StringIO()
    call_command("good", stdout=output)

    assert output.getvalue().strip() == "Arthexis is Good"


def test_good_command_prints_star_only_for_minor_issues(monkeypatch) -> None:
    """Minor-only reports should stay concise unless details are requested."""

    monkeypatch.setattr(
        "apps.core.management.commands.good.build_good_report",
        lambda: GoodReport(
            issues=(
                GoodIssue(
                    "minor",
                    "Optional feature disabled",
                    "detail",
                    "minor",
                    "features",
                ),
            )
        ),
    )

    output = StringIO()
    call_command("good", stdout=output)

    assert output.getvalue().strip() == "Arthexis is Good*"


def test_good_command_details_reveal_minor_issues(monkeypatch) -> None:
    """The details flag should show minor readiness considerations."""

    monkeypatch.setattr(
        "apps.core.management.commands.good.build_good_report",
        lambda: GoodReport(
            issues=(
                GoodIssue(
                    "minor",
                    "Optional feature disabled",
                    "detail",
                    "minor",
                    "features",
                ),
            )
        ),
    )

    output = StringIO()
    call_command("good", "--details", stdout=output)
    rendered = output.getvalue()

    assert "Arthexis is Good*" in rendered
    assert "Minor considerations:" in rendered
    assert "Optional feature disabled" in rendered


def test_good_command_lists_ranked_important_issues(monkeypatch) -> None:
    """Important issues should be listed in ranked order."""

    monkeypatch.setattr(
        "apps.core.management.commands.good.build_good_report",
        lambda: GoodReport(
            issues=(
                GoodIssue("critical", "Tests failed", "detail", "critical", "tests"),
                GoodIssue("minor", "Feature missing", "detail", "minor", "features"),
            )
        ),
    )

    output = StringIO()
    call_command("good", stdout=output)
    rendered = output.getvalue()

    assert "Issues to consider (highest priority first):" in rendered
    assert "[CRITICAL] Tests failed" in rendered
    assert "[MINOR] Feature missing" in rendered
