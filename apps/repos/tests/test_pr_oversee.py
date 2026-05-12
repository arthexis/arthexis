from __future__ import annotations

import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.repos.pr_oversee import (
    CommandResult,
    PullRequestOverseeError,
    PullRequestOverseer,
    _local_venv_link,
    changed_files_to_test_plan,
    default_patchwork_dir,
    dependency_duplicates,
    hygiene_report,
    patchwork_worktree_path,
    readiness_gate,
    review_reply_summary,
)


class FakeRunner:
    def __init__(self, responses: list[CommandResult] | None = None) -> None:
        self.responses = list(responses or [])
        self.commands: list[list[str]] = []
        self.cwd_history: list[Path | None] = []

    def run(
        self, command: list[str], *, cwd: Path | None = None, check: bool = False
    ) -> CommandResult:
        self.commands.append(command)
        self.cwd_history.append(cwd)
        if command[:3] == ["git", "worktree", "add"]:
            Path(command[-2]).mkdir(parents=True, exist_ok=True)
        result = (
            self.responses.pop(0) if self.responses else CommandResult(returncode=0)
        )
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


def _review_threads_payload(*, unresolved: bool = False):
    nodes = []
    if unresolved:
        nodes.append(
            {
                "isResolved": False,
                "isOutdated": False,
                "path": "apps/repos/pr_oversee.py",
                "line": 42,
                "comments": {"nodes": []},
            }
        )
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }


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


def test_readiness_gate_ignores_superseded_cancelled_check_runs():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "name": "Upgrade safety gate",
                    "workflowName": "Upgrade Gate",
                    "status": "COMPLETED",
                    "conclusion": "CANCELLED",
                    "completedAt": "2026-05-12T18:00:00Z",
                },
                {
                    "name": "Upgrade safety gate",
                    "workflowName": "Upgrade Gate",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "completedAt": "2026-05-12T18:05:00Z",
                },
            ],
        )
    )

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["checks"]["failing"] == []
    assert result["checks"]["superseded"][0]["value"] == "CANCELLED"


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
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
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


def test_comments_paginates_review_threads():
    first_page = {
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
                                "comments": {"nodes": []},
                            }
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                }
            }
        }
    }
    second_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "apps/repos/management/commands/pr_oversee.py",
                                "line": 12,
                                "comments": {"nodes": []},
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(first_page)),
            CommandResult(0, json.dumps(second_page)),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.comments(123)

    assert result["unresolvedCount"] == 2
    assert len(result["threads"]) == 2
    assert runner.commands[1][-1] == "after=cursor-1"


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
    assert [
        sys.executable,
        "manage.py",
        "test",
        "run",
        "--",
        "apps/repos/tests",
    ] in result["commands"]
    assert [
        sys.executable,
        "manage.py",
        "makemigrations",
        "--check",
        "--dry-run",
    ] in result["commands"]
    assert result["notes"] == [
        "Workflow files changed; inspect GitHub Actions syntax and required checks."
    ]


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
    assert runner.commands[-1] == [
        "gh",
        "run",
        "view",
        "42",
        "--repo",
        "arthexis/arthexis",
        "--log-failed",
    ]


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


def test_dependency_duplicates_groups_versioned_dependabot_branches():
    result = dependency_duplicates(
        [
            {
                "number": 1,
                "title": "build(deps): update django",
                "author": {"login": "dependabot[bot]"},
                "headRefName": "dependabot/pip/django-v5.2.12",
                "updatedAt": "2026-05-01T00:00:00Z",
            },
            {
                "number": 2,
                "title": "build(deps): update django",
                "author": {"login": "dependabot[bot]"},
                "headRefName": "dependabot/pip/django-v5.2.13",
                "updatedAt": "2026-05-02T00:00:00Z",
            },
        ]
    )

    assert result["django"]["items"][0]["targetVersion"] == "5.2.12"
    assert result["django"]["preferred"]["number"] == 2


def test_advance_includes_drafts_and_prioritizes_ready_work():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "number": 123,
                            "title": "Ready PR",
                            "isDraft": False,
                        },
                        {
                            "number": 124,
                            "title": "Draft PR",
                            "isDraft": True,
                        },
                    ]
                ),
            ),
            CommandResult(0, json.dumps(_pr_payload(number=123, title="Ready PR"))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
            CommandResult(
                0,
                json.dumps(_pr_payload(number=124, title="Draft PR", isDraft=True)),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.advance(limit=2, include_drafts=True)

    assert result["consideredCount"] == 2
    assert [item["number"] for item in result["topSuggestions"]] == [123, 124]
    assert result["topSuggestions"][0]["readyToMerge"] is True
    assert result["topSuggestions"][1]["canMarkReady"] is True


def test_advance_suggests_ci_for_pending_checks():
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=FakeRunner())

    command = overseer._advance_suggested_command(
        123,
        gate={},
        ready_to_merge=False,
        can_mark_ready=False,
        blockers=["pending:Tests:IN_PROGRESS"],
        require_approval=False,
        allow_pending=False,
        delete_branch=False,
        admin=False,
    )

    assert command.endswith("ci --pr 123 --failures --logs")


def test_advance_merge_suggestion_mirrors_enabled_flags():
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=FakeRunner())

    command = overseer._advance_suggested_command(
        123,
        gate={"headRefOid": "head-sha"},
        ready_to_merge=True,
        can_mark_ready=False,
        blockers=[],
        require_approval=True,
        allow_pending=True,
        delete_branch=False,
        admin=True,
    )

    assert "--delete-branch" not in command
    assert "--require-approval" in command
    assert "--allow-pending" in command
    assert "--admin" in command
    assert "--expected-head-sha head-sha" in command


def test_checkout_fetches_pr_head_creates_worktree_and_metadata(tmp_path: Path):
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload())),
            CommandResult(0),
            CommandResult(0),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )
    worktree = tmp_path / "pr-123"

    result = overseer.checkout(123, worktree=worktree, branch="repos-pr-123")

    assert result["worktree"] == str(worktree)
    assert runner.commands[1] == [
        "git",
        "fetch",
        "origin",
        "pull/123/head:refs/remotes/origin/pr/123",
    ]
    assert runner.commands[2] == [
        "git",
        "worktree",
        "add",
        "-b",
        "repos-pr-123",
        str(worktree),
        "refs/remotes/origin/pr/123",
    ]
    assert (
        json.loads((worktree / ".arthexis-pr-oversee.json").read_text())["headRefOid"]
        == "head-sha"
    )


def test_checkout_links_current_venv_into_worktree(tmp_path: Path):
    (tmp_path / ".venv").mkdir()
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload())),
            CommandResult(0),
            CommandResult(0),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )
    worktree = tmp_path / "pr-123"

    result = overseer.checkout(123, worktree=worktree, branch="repos-pr-123")

    assert result["venv"]["linked"] is True
    assert (worktree / ".venv").exists()


def test_checkout_does_not_follow_metadata_symlink(tmp_path: Path):
    class SymlinkRunner(FakeRunner):
        def run(
            self, command: list[str], *, cwd: Path | None = None, check: bool = False
        ) -> CommandResult:
            result = super().run(command, cwd=cwd, check=check)
            if command[:3] == ["git", "worktree", "add"]:
                outside_target = tmp_path / "outside.txt"
                outside_target.write_text("sensitive\n", encoding="utf-8")
                (Path(command[-2]) / ".arthexis-pr-oversee.json").symlink_to(
                    outside_target
                )
            return result

    runner = SymlinkRunner(
        [
            CommandResult(0, json.dumps(_pr_payload())),
            CommandResult(0),
            CommandResult(0),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )
    worktree = tmp_path / "pr-123"
    outside_target = tmp_path / "outside.txt"

    result = overseer.checkout(123, worktree=worktree, branch="repos-pr-123")

    assert result["metadataWriteError"] is True
    assert outside_target.read_text(encoding="utf-8") == "sensitive\n"


def test_checkout_writes_metadata_when_no_no_follow_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload())),
            CommandResult(0),
            CommandResult(0),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )
    worktree = tmp_path / "pr-123"

    result = overseer.checkout(123, worktree=worktree, branch="repos-pr-123")

    assert "metadataWriteError" not in result
    assert (
        json.loads((worktree / ".arthexis-pr-oversee.json").read_text())["headRefOid"]
        == "head-sha"
    )


def test_patchwork_worktree_path_is_deterministic(tmp_path: Path):
    assert patchwork_worktree_path(tmp_path, "arthexis/arthexis", 123) == (
        tmp_path / "arthexis-arthexis-pr-123"
    )


def test_default_patchwork_dir_respects_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ARTHEXIS_PATCHWORK_DIR", str(tmp_path))

    assert default_patchwork_dir() == tmp_path


def test_merge_gates_expected_head_before_calling_gh_merge():
    comments = {
        "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}
    }
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
        "--match-head-commit",
        "head-sha",
        "--delete-branch",
    ]


def test_cleanup_refuses_unmerged_pr():
    runner = FakeRunner([CommandResult(0, json.dumps(_pr_payload(state="OPEN")))])
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    with pytest.raises(PullRequestOverseeError, match="not merged"):
        overseer.cleanup(123)


def test_cleanup_fetches_merged_pr_base_branch(tmp_path: Path):
    runner = FakeRunner(
        [CommandResult(0, json.dumps(_pr_payload(state="MERGED", baseRefName="trunk")))]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer.cleanup(123)

    assert runner.commands[1] == ["git", "fetch", "origin", "trunk", "--prune"]
    assert result["actions"][0] == {
        "action": "fetch-base-prune",
        "branch": "trunk",
        "returncode": 0,
    }


def test_cleanup_forces_removal_for_owned_patchwork_metadata(tmp_path: Path):
    worktree = tmp_path / "patchwork" / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    (worktree / ".arthexis-pr-oversee.json").write_text('{"number": 123}\n')
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload(state="MERGED"))),
            CommandResult(128, stderr="contains modified or untracked files"),
            CommandResult(0, "?? .arthexis-pr-oversee.json\n?? .venv/\n"),
            CommandResult(0),
            CommandResult(0),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer.cleanup(123, worktree=worktree)

    assert result["actions"][0]["forced"] is True
    assert runner.commands[3] == [
        "git",
        "worktree",
        "remove",
        "--force",
        str(worktree),
    ]


def test_patchwork_remove_prunes_owned_residue_after_missing_worktree_error(
    tmp_path: Path,
):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    venv_source = tmp_path / "venv-source"
    venv_source.mkdir()
    venv = _local_venv_link(venv_source, worktree / ".venv")
    assert venv["linked"] is True
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"number": 123, "venv": venv})
    )
    runner = FakeRunner(
        [
            CommandResult(128, stderr="is not a working tree"),
            CommandResult(128, stderr="not a git repository"),
            CommandResult(128, stderr="is not a working tree"),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["localRemove"]["removed"] is True
    assert not worktree.exists()


def test_patchwork_remove_preserves_real_venv_residue(tmp_path: Path):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    (worktree / ".arthexis-pr-oversee.json").write_text('{"number": 123}\n')
    (worktree / ".venv").mkdir()
    runner = FakeRunner(
        [
            CommandResult(128, stderr="is not a working tree"),
            CommandResult(128, stderr="not a git repository"),
            CommandResult(128, stderr="is not a working tree"),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["localRemove"]["reason"] == "non-owned-residue"
    assert result["localRemove"]["paths"] == [".venv"]
    assert (worktree / ".venv").exists()


def test_patchwork_remove_respects_git_force_failure(tmp_path: Path):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    (worktree / ".arthexis-pr-oversee.json").write_text('{"number": 123}\n')
    runner = FakeRunner(
        [
            CommandResult(128, stderr="contains modified or untracked files"),
            CommandResult(0, "?? .arthexis-pr-oversee.json\n"),
            CommandResult(128, stderr="worktree is locked"),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["forced"] is True
    assert result["forceReturncode"] == 128
    assert worktree.exists()
    assert len(runner.commands) == 3


def test_patchwork_hygiene_marks_merged_worktrees_for_prune(tmp_path: Path):
    worktree = tmp_path / "arthexis-arthexis-pr-123"
    worktree.mkdir()
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"repo": "arthexis/arthexis", "number": 123})
    )
    runner = FakeRunner(
        [CommandResult(0, json.dumps([{"number": 123, "state": "MERGED"}]))]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.patchwork_hygiene(root=tmp_path)

    assert result["items"][0]["candidate"] is True
    assert result["items"][0]["reason"] == "prune"
    assert runner.commands[0][:6] == [
        "gh",
        "pr",
        "list",
        "--repo",
        "arthexis/arthexis",
        "--state",
    ]


def test_patchwork_hygiene_batches_pr_state_lookup(tmp_path: Path):
    for number in (123, 124):
        worktree = tmp_path / f"arthexis-arthexis-pr-{number}"
        worktree.mkdir()
        (worktree / ".arthexis-pr-oversee.json").write_text(
            json.dumps({"repo": "arthexis/arthexis", "number": number})
        )
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {"number": 123, "state": "MERGED"},
                        {"number": 124, "state": "OPEN"},
                    ]
                ),
            )
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.patchwork_hygiene(root=tmp_path)

    assert [item["state"] for item in result["items"]] == ["MERGED", "OPEN"]
    assert len(runner.commands) == 1


def test_patchwork_hygiene_marks_invalid_metadata_without_crashing(tmp_path: Path):
    worktree = tmp_path / "arthexis-arthexis-pr-bad"
    worktree.mkdir()
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"repo": "arthexis/arthexis", "number": "abc"})
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=FakeRunner())

    result = overseer.patchwork_hygiene(root=tmp_path)

    assert result["items"][0]["candidate"] is False
    assert result["items"][0]["reason"] == "invalid-pr-number"


def test_hygiene_detects_missing_migration_and_generated_files():
    result = hygiene_report(
        _pr_payload(body="No sections"),
        ["apps/repos/models/review.py", "apps/repos/__pycache__/x.pyc"],
    )

    assert result["ok"] is False
    assert "model-change:missing-migration" in result["failures"]
    assert "body:missing-summary" in result["warnings"]
    assert "body:missing-validation" in result["warnings"]


def test_review_reply_summary_formats_change_and_validation_body():
    result = review_reply_summary(
        commit="0123456789abcdef",
        changes=["Linked patchwork .venv"],
        validations=["manage.py test run -- apps/repos/tests"],
    )

    assert result["commit"] == "0123456789ab"
    assert "Addressed in 0123456789ab." in result["body"]
    assert "- Linked patchwork .venv" in result["body"]
    assert "- manage.py test run -- apps/repos/tests" in result["body"]


def test_management_command_merge_without_write_reports_plan():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.gate = Mock(return_value={"ready": True, "blockers": [], "warnings": []})
    fake.merge = Mock(
        side_effect=AssertionError("merge should not run in dry-run mode")
    )
    buffer = StringIO()

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        call_command(
            "pr_oversee",
            "--repo",
            "arthexis/arthexis",
            "--json",
            "merge",
            "--pr",
            "123",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["write"] is False
    assert payload["plannedCommand"] == "gh pr merge"
    assert payload["gate"]["ready"] is True
    fake.gate.assert_called_once_with(
        123,
        require_approval=False,
        allow_pending=False,
    )
    fake.merge.assert_not_called()


def test_management_command_checkout_defaults_to_patchwork_dir(tmp_path: Path):
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.checkout = Mock(
        return_value={
            "number": 123,
            "worktree": str(tmp_path / "arthexis-arthexis-pr-123"),
        }
    )
    buffer = StringIO()

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        call_command(
            "pr_oversee",
            "--repo",
            "arthexis/arthexis",
            "--json",
            "checkout",
            "--pr",
            "123",
            "--patchwork-dir",
            str(tmp_path),
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["worktree"] == str(tmp_path / "arthexis-arthexis-pr-123")
    fake.checkout.assert_called_once()
    _, kwargs = fake.checkout.call_args
    assert kwargs["worktree"] == tmp_path / "arthexis-arthexis-pr-123"


def test_management_command_advance_passes_include_drafts_and_write_flags():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.advance = Mock(
        return_value={
            "repo": "arthexis/arthexis",
            "includeDrafts": True,
            "topSuggestions": [],
            "items": [],
            "actions": [],
        }
    )
    buffer = StringIO()

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        call_command(
            "pr_oversee",
            "--repo",
            "arthexis/arthexis",
            "--json",
            "advance",
            "--include-drafts",
            "--ready-drafts",
            "--merge",
            "--write",
            "--limit",
            "5",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["includeDrafts"] is True
    fake.advance.assert_called_once()
    _, kwargs = fake.advance.call_args
    assert kwargs["limit"] == 5
    assert kwargs["include_drafts"] is True
    assert kwargs["ready_drafts"] is True
    assert kwargs["merge"] is True
    assert kwargs["write"] is True


def test_monitor_stops_for_manual_review_blocker():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(_pr_payload(reviewDecision="CHANGES_REQUESTED")),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis",
        runner=runner,
        sleep_func=lambda _seconds: None,
    )

    result = overseer.monitor(
        123,
        interval_seconds=0,
        max_iterations=1,
        dependency_limit=0,
    )

    assert result["status"] == "manual_decision_required"
    assert result["manualDecisionRequired"] is True
    assert "gate:review:CHANGES_REQUESTED" in result["manualDecisionReasons"]
    assert result["iterationCount"] == 1


def test_monitor_waits_on_pending_then_merges_and_cleans():
    pending = _pr_payload(
        statusCheckRollup=[{"name": "Tests", "status": "IN_PROGRESS", "conclusion": ""}]
    )
    ready = _pr_payload()
    merged = _pr_payload(state="MERGED")
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(pending)),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
            CommandResult(0, json.dumps(ready)),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, json.dumps(ready)),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "merged"),
            CommandResult(0, json.dumps(merged)),
            CommandResult(0, json.dumps(merged)),
            CommandResult(0, ""),
        ]
    )
    sleeps = []
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis",
        runner=runner,
        sleep_func=sleeps.append,
    )

    result = overseer.monitor(
        123,
        interval_seconds=0,
        max_iterations=2,
        dependency_limit=0,
        merge=True,
        cleanup=True,
        write=True,
        delete_branch=True,
    )

    assert result["status"] == "complete"
    assert result["complete"] is True
    assert result["iterationCount"] == 2
    assert sleeps == [0]
    diff_count = sum(
        1 for command in runner.commands if command[:3] == ["gh", "pr", "diff"]
    )
    assert diff_count == 1
    assert [action["action"] for action in result["actions"]] == [
        "merge",
        "cleanup",
    ]
    assert runner.commands[7] == [
        "gh",
        "pr",
        "merge",
        "123",
        "--repo",
        "arthexis/arthexis",
        "--squash",
        "--match-head-commit",
        "head-sha",
        "--delete-branch",
    ]


def test_monitor_validates_in_reused_worktree_before_merge(tmp_path: Path):
    worktree = tmp_path / "pr-123"
    worktree.mkdir()
    ready = _pr_payload()
    merged = _pr_payload(state="MERGED")
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(ready)),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
            CommandResult(0),
            CommandResult(0),
            CommandResult(0, "check passed"),
            CommandResult(0, "tests passed"),
            CommandResult(0, json.dumps(ready)),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "merged"),
            CommandResult(0, json.dumps(merged)),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.monitor(
        123,
        interval_seconds=0,
        max_iterations=1,
        dependency_limit=0,
        worktree=worktree,
        run_test_plan=True,
        merge=True,
        write=True,
    )

    assert result["status"] == "complete"
    assert result["actions"][0] == {
        "action": "checkout-reuse",
        "worktree": str(worktree),
    }
    assert result["actions"][1]["action"] == "sync-worktree"
    assert result["actions"][1]["headRefOid"] == "head-sha"
    assert result["actions"][2]["action"] == "local-validation"
    assert result["actions"][2]["cwd"] == str(worktree)
    assert result["last"]["localValidation"]["cwd"] == str(worktree)
    assert runner.cwd_history[5] == worktree
    assert runner.cwd_history[6] == worktree


def test_monitor_requires_write_for_run_test_plan():
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload())),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    with pytest.raises(PullRequestOverseeError) as exc:
        overseer.monitor(123, run_test_plan=True, max_iterations=1, dependency_limit=0)

    assert (
        str(exc.value)
        == "monitor --run-test-plan executes local code and requires --write"
    )
    assert [command[:3] for command in runner.commands] == [
        ["gh", "pr", "view"],
        ["gh", "api", "graphql"],
        ["gh", "pr", "diff"],
    ]


def test_monitor_resyncs_reused_worktree_when_pr_head_changes(tmp_path: Path):
    worktree = tmp_path / "pr-123"
    worktree.mkdir()
    first_head = _pr_payload(
        headRefOid="head-one",
        statusCheckRollup=[
            {"name": "Tests", "status": "IN_PROGRESS", "conclusion": ""}
        ],
    )
    second_head = _pr_payload(headRefOid="head-two")
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(first_head)),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
            CommandResult(0),
            CommandResult(0),
            CommandResult(0, "check passed"),
            CommandResult(0, "tests passed"),
            CommandResult(0, json.dumps(second_head)),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
            CommandResult(0),
            CommandResult(0),
            CommandResult(0, "check passed"),
            CommandResult(0, "tests passed"),
        ]
    )
    sleeps = []
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis",
        runner=runner,
        sleep_func=sleeps.append,
    )

    result = overseer.monitor(
        123,
        interval_seconds=0,
        max_iterations=2,
        dependency_limit=0,
        worktree=worktree,
        run_test_plan=True,
        write=True,
    )

    sync_heads = [
        action["headRefOid"]
        for action in result["actions"]
        if action["action"] == "sync-worktree"
    ]
    assert sync_heads == ["head-one", "head-two"]
    assert result["manualDecisionReasons"] == ["merge_decision_required"]
    assert sleeps == [0]


def test_monitor_skips_validation_for_already_merged_missing_patchwork(tmp_path: Path):
    worktree = tmp_path / "missing-patchwork"
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload(state="MERGED"))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/repos/pr_oversee.py\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.monitor(
        123,
        interval_seconds=0,
        max_iterations=1,
        dependency_limit=0,
        worktree=worktree,
        run_test_plan=True,
    )

    assert result["status"] == "complete"
    assert "localValidation" not in result["last"]
    assert worktree not in runner.cwd_history


def test_management_command_monitor_invokes_overseer_monitor():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.monitor = Mock(
        return_value={
            "status": "complete",
            "complete": True,
            "manualDecisionRequired": False,
            "manualDecisionReasons": [],
        }
    )
    buffer = StringIO()

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        call_command(
            "pr_oversee",
            "--repo",
            "arthexis/arthexis",
            "--json",
            "monitor",
            "--pr",
            "123",
            "--interval",
            "0",
            "--max-iterations",
            "1",
            "--merge",
            "--write",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["status"] == "complete"
    fake.monitor.assert_called_once()
    _, kwargs = fake.monitor.call_args
    assert kwargs["interval_seconds"] == 0
    assert kwargs["max_iterations"] == 1
    assert kwargs["merge"] is True
    assert kwargs["write"] is True


def test_management_command_monitor_defaults_validation_to_patchwork(tmp_path: Path):
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.monitor = Mock(
        return_value={
            "status": "manual_decision_required",
            "complete": False,
            "manualDecisionRequired": True,
            "manualDecisionReasons": ["merge_decision_required"],
        }
    )
    buffer = StringIO()

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError):
            call_command(
                "pr_oversee",
                "--repo",
                "arthexis/arthexis",
                "--json",
                "monitor",
                "--pr",
                "123",
                "--run-test-plan",
                "--patchwork-dir",
                str(tmp_path),
                stdout=buffer,
            )

    _, kwargs = fake.monitor.call_args
    assert kwargs["worktree"] == tmp_path / "arthexis-arthexis-pr-123"
