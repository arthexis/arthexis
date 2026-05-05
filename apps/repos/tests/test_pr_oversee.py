from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.repos.pr_oversee import (
    CommandResult,
    PullRequestOverseeError,
    PullRequestOverseer,
    changed_files_to_test_plan,
    dependency_duplicates,
    hygiene_report,
    readiness_gate,
)


class FakeRunner:
    def __init__(self, responses: list[CommandResult] | None = None) -> None:
        self.responses = list(responses or [])
        self.commands: list[list[str]] = []

    def run(self, command: list[str], *, cwd: Path | None = None, check: bool = False) -> CommandResult:
        self.commands.append(command)
        if command[:3] == ["git", "worktree", "add"]:
            Path(command[-2]).mkdir(parents=True, exist_ok=True)
        result = self.responses.pop(0) if self.responses else CommandResult(returncode=0)
        if check and result.returncode != 0:
            raise PullRequestOverseeError(result.stderr or result.stdout or "failed")
        return result


def _pr_payload(**overrides):
    payload = {
        "number": 123,
        "title": "Add deterministic PR oversee",
        "author": {"login": "alice"},
        "body": "Summary\n\nValidation\n\nFixes #1",
        "baseRefName": "main",
        "baseRefOid": "base-sha",
        "headRefName": "repos-pr-oversee-cli",
        "headRefOid": "head-sha",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE",
        "reviewDecision": "APPROVED",
        "state": "OPEN",
        "statusCheckRollup": [
            {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ],
        "updatedAt": "2026-05-05T18:00:00Z",
        "url": "https://github.com/arthexis/arthexis/pull/123",
    }
    payload.update(overrides)
    return payload


def test_readiness_gate_reports_blockers_for_review_checks_and_threads():
    result = readiness_gate(
        _pr_payload(
            reviewDecision="CHANGES_REQUESTED",
            statusCheckRollup=[
                {"name": "Tests", "status": "COMPLETED", "conclusion": "FAILURE"},
                {"name": "CodeQL", "status": "IN_PROGRESS", "conclusion": ""},
            ],
            unresolvedReviewThreadCount=2,
        )
    )

    assert result["ready"] is False
    assert "review:CHANGES_REQUESTED" in result["blockers"]
    assert "check:Tests:FAILURE" in result["blockers"]
    assert "pending:CodeQL:IN_PROGRESS" in result["blockers"]
    assert "review_threads:UNRESOLVED:2" in result["blockers"]


def test_comments_normalizes_unresolved_review_threads():
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "apps/repos/pr_oversee.py",
                                "line": 42,
                                "comments": {
                                    "nodes": [
                                        {
                                            "author": {"login": "reviewer"},
                                            "body": "Please cover failures.",
                                            "createdAt": "2026-05-05T18:01:00Z",
                                            "url": "https://example.test/comment",
                                        }
                                    ]
                                },
                            },
                            {
                                "isResolved": True,
                                "isOutdated": False,
                                "path": "docs/x.md",
                                "line": 1,
                                "comments": {"nodes": []},
                            },
                        ]
                    }
                }
            }
        }
    }
    runner = FakeRunner([CommandResult(0, json.dumps(payload))])
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.comments(123, unresolved_only=True)

    assert result["unresolvedCount"] == 1
    assert result["threads"][0]["path"] == "apps/repos/pr_oversee.py"
    assert result["threads"][0]["comments"][0]["author"] == "reviewer"


def test_test_plan_maps_changed_apps_and_migrations_to_commands():
    result = changed_files_to_test_plan(
        [
            "apps/repos/pr_oversee.py",
            "apps/repos/models/review.py",
            "apps/repos/migrations/0005_review.py",
            ".github/workflows/test.yml",
        ]
    )

    assert result["apps"] == ["repos"]
    assert result["modelChange"] is True
    assert result["migrationChange"] is True
    assert [".venv/bin/python", "manage.py", "test", "run", "--", "apps/repos/tests"] in result["commands"]
    assert [".venv/bin/python", "manage.py", "makemigrations", "--check", "--dry-run"] in result["commands"]
    assert result["notes"] == ["Workflow files changed; inspect GitHub Actions syntax and required checks."]


def test_ci_failures_collects_failed_run_log_snippet():
    pr = _pr_payload(
        statusCheckRollup=[
            {
                "name": "Tests",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "detailsUrl": "https://github.com/arthexis/arthexis/actions/runs/42/job/99",
            }
        ]
    )
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(pr)),
            CommandResult(0, "failed test log"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.ci_failures(123, include_logs=True)

    assert result["failures"][0]["name"] == "Tests"
    assert result["logs"] == {"Tests": "failed test log"}
    assert runner.commands[-1] == ["gh", "run", "view", "42", "--repo", "arthexis/arthexis", "--log-failed"]


def test_dependency_duplicates_marks_older_updates_superseded():
    result = dependency_duplicates(
        [
            {
                "number": 1,
                "title": "Bump django from 5.2.11 to 5.2.12",
                "author": {"login": "dependabot[bot]"},
                "headRefName": "dependabot/pip/django-5.2.12",
                "updatedAt": "2026-05-01T00:00:00Z",
            },
            {
                "number": 2,
                "title": "Bump django from 5.2.11 to 5.2.13",
                "author": {"login": "dependabot[bot]"},
                "headRefName": "dependabot/pip/django-5.2.13",
                "updatedAt": "2026-05-02T00:00:00Z",
            },
        ]
    )

    assert result["django"]["superseded"][0]["number"] == 1
    assert result["django"]["preferred"]["number"] == 2


def test_checkout_fetches_pr_head_creates_worktree_and_metadata(tmp_path: Path):
    runner = FakeRunner([CommandResult(0, json.dumps(_pr_payload())), CommandResult(0), CommandResult(0)])
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner, cwd=tmp_path)
    worktree = tmp_path / "pr-123"

    result = overseer.checkout(123, worktree=worktree, branch="repos-pr-123")

    assert result["worktree"] == str(worktree)
    assert runner.commands[1] == ["git", "fetch", "origin", "pull/123/head:refs/remotes/origin/pr/123"]
    assert runner.commands[2] == [
        "git",
        "worktree",
        "add",
        "-b",
        "repos-pr-123",
        str(worktree),
        "refs/remotes/origin/pr/123",
    ]
    assert json.loads((worktree / ".arthexis-pr-oversee.json").read_text())["headRefOid"] == "head-sha"


def test_merge_gates_expected_head_before_calling_gh_merge():
    comments = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
    merged = _pr_payload(state="MERGED")
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload())),
            CommandResult(0, json.dumps(comments)),
            CommandResult(0, "merged"),
            CommandResult(0, json.dumps(merged)),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.merge(123, expected_head_sha="head-sha", delete_branch=True)

    assert result["merged"] is True
    assert runner.commands[2] == [
        "gh",
        "pr",
        "merge",
        "123",
        "--repo",
        "arthexis/arthexis",
        "--squash",
        "--delete-branch",
    ]


def test_cleanup_refuses_unmerged_pr():
    runner = FakeRunner([CommandResult(0, json.dumps(_pr_payload(state="OPEN")))])
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    with pytest.raises(PullRequestOverseeError, match="not merged"):
        overseer.cleanup(123)


def test_hygiene_detects_missing_migration_and_generated_files():
    result = hygiene_report(
        _pr_payload(body="No sections"),
        ["apps/repos/models/review.py", "apps/repos/__pycache__/x.pyc"],
    )

    assert result["ok"] is False
    assert "model-change:missing-migration" in result["failures"]
    assert "body:missing-summary" in result["warnings"]
    assert "body:missing-validation" in result["warnings"]


def test_management_command_merge_without_write_reports_plan():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.gate = lambda *args, **kwargs: {"ready": True, "blockers": [], "warnings": []}

    with patch("apps.repos.management.commands.pr_oversee.PullRequestOverseer", return_value=fake):
        output = call_command(
            "pr_oversee",
            "--repo",
            "arthexis/arthexis",
            "--json",
            "merge",
            "--pr",
            "123",
        )

    assert output is None
