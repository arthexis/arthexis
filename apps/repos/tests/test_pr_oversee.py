from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError

from apps.nodes.models import Node, NodeRole
from apps.repos import pr_oversee
from apps.repos.management.commands.pr_oversee import Command as PrOverseeCommand
from apps.repos.models import GitHubRepository, RepositoryWorkAssignment
from apps.repos.pr_oversee.affinity import infer_work_profile, score_node_affinity
from apps.repos.pr_oversee.checks import dependency_duplicates, readiness_gate
from apps.repos.pr_oversee.hygiene import (
    affected_install_shard,
    changed_files_to_test_plan,
    hygiene_report,
    render_test_plan_markdown,
)
from apps.repos.pr_oversee.reply import review_reply_summary
from apps.repos.pr_oversee.service import PullRequestOverseer, parse_pr_dependency_edges
from apps.repos.pr_oversee.types import CommandResult, PullRequestOverseeError
from apps.repos.pr_oversee.worktree import (
    _local_venv_link,
    _path_is_relative_to,
    default_patchwork_dir,
    patchwork_worktree_path,
)
from apps.repos.services import work_assignments


def test_package_reexports_symbol_identity_and_all_contract():
    expected_exports = {
        "CommandResult",
        "PullRequestOverseeError",
        "PullRequestOverseer",
        "affected_install_shard",
        "changed_files_to_test_plan",
        "dependency_duplicates",
        "hygiene_report",
        "render_test_plan_markdown",
        "infer_work_profile",
        "is_advisory_check",
        "readiness_gate",
        "review_reply_summary",
        "score_node_affinity",
    }

    assert pr_oversee.CommandResult is CommandResult
    assert pr_oversee.PullRequestOverseeError is PullRequestOverseeError
    assert pr_oversee.PullRequestOverseer is PullRequestOverseer
    assert pr_oversee.affected_install_shard is affected_install_shard
    assert pr_oversee.changed_files_to_test_plan is changed_files_to_test_plan
    assert pr_oversee.dependency_duplicates is dependency_duplicates
    assert pr_oversee.hygiene_report is hygiene_report
    assert pr_oversee.render_test_plan_markdown is render_test_plan_markdown
    assert pr_oversee.infer_work_profile is infer_work_profile
    assert not pr_oversee.is_advisory_check(
        {"name": "SonarCloud Code Analysis", "conclusion": "FAILURE"}
    )
    assert not pr_oversee.is_advisory_check(
        {
            "__typename": "CheckRun",
            "name": "SonarCloud Code Analysis",
            "detailsUrl": "https://sonarcloud.io/dashboard?id=arthexis_arthexis",
            "conclusion": "FAILURE",
        }
    )
    assert not pr_oversee.is_advisory_check(
        {
            "__typename": "StatusContext",
            "context": "SonarCloud Code Analysis",
            "targetUrl": "https://sonarcloud.io/dashboard?id=arthexis_arthexis",
            "state": "FAILURE",
        }
    )
    assert pr_oversee.is_advisory_check(
        {
            "name": "SonarCloud Code Analysis",
            "app": {"name": "SonarCloud"},
            "conclusion": "FAILURE",
        }
    )
    assert pr_oversee.is_advisory_check(
        {
            "name": "SonarCloud Code Analysis",
            "app": {"name": "SonarQubeCloud"},
            "conclusion": "FAILURE",
        }
    )
    assert not pr_oversee.is_advisory_check(
        {
            "name": "Tests",
            "app": {"name": "SonarQubeCloud"},
            "conclusion": "FAILURE",
        }
    )
    assert not pr_oversee.is_advisory_check(
        {
            "name": "SonarCloud Code Analysis",
            "app": {"name": "GitHub Actions"},
            "conclusion": "FAILURE",
        }
    )
    assert not pr_oversee.is_advisory_check({"name": "my-sonarcloud-spoof"})
    assert not pr_oversee.is_advisory_check({"name": None, "context": None})
    assert pr_oversee.readiness_gate is readiness_gate
    assert pr_oversee.review_reply_summary is review_reply_summary
    assert pr_oversee.score_node_affinity is score_node_affinity
    assert expected_exports <= set(pr_oversee.__all__)


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


def test_pr_view_uses_gh_fields_supported_by_current_cli_and_fills_base_sha():
    payload = _pr_payload()
    payload.pop("baseRefOid")
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(payload)),
            CommandResult(0, "base-from-origin\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.pr_view(123)

    assert result["baseRefOid"] == "base-from-origin"
    assert "baseRefOid" not in runner.commands[0][-1]
    assert runner.commands[1] == [
        "git",
        "rev-parse",
        "--verify",
        "refs/remotes/origin/main^{commit}",
    ]


def test_pr_view_enriches_sonar_check_run_app_identity():
    details_url = "https://sonarcloud.io/dashboard?id=arthexis_arthexis"
    payload = _pr_payload(
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "SonarCloud Code Analysis",
                "detailsUrl": details_url,
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            }
        ]
    )
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(payload)),
            CommandResult(
                0,
                json.dumps(
                    {
                        "check_runs": [
                            {
                                "name": "SonarCloud Code Analysis",
                                "details_url": details_url,
                                "app": {"name": "SonarQubeCloud"},
                            }
                        ]
                    }
                ),
            ),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.pr_view(123)

    sonar_check = result["statusCheckRollup"][0]
    assert sonar_check["app"] == {"name": "SonarQubeCloud"}
    gate = readiness_gate(result)
    assert gate["ready"] is True
    assert gate["checks"]["advisory"][0]["name"] == "SonarCloud Code Analysis"
    assert runner.commands[1] == [
        "gh",
        "api",
        "repos/arthexis/arthexis/commits/head-sha/check-runs?per_page=100&page=1&filter=all",
    ]


def test_pr_view_paginates_sonar_check_run_app_identity_lookup():
    details_url = "https://sonarcloud.io/dashboard?id=arthexis_arthexis"
    payload = _pr_payload(
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "SonarCloud Code Analysis",
                "detailsUrl": details_url,
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            }
        ]
    )
    first_page_runs = [
        {
            "name": f"Generic Check {index}",
            "details_url": f"https://example.test/checks/{index}",
            "app": {"name": "GitHub Actions"},
        }
        for index in range(100)
    ]
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(payload)),
            CommandResult(0, json.dumps({"check_runs": first_page_runs})),
            CommandResult(
                0,
                json.dumps(
                    {
                        "check_runs": [
                            {
                                "name": "SonarCloud Code Analysis",
                                "details_url": details_url,
                                "app": {"name": "SonarQubeCloud"},
                            }
                        ]
                    }
                ),
            ),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.pr_view(123)

    sonar_check = result["statusCheckRollup"][0]
    assert sonar_check["app"] == {"name": "SonarQubeCloud"}
    assert runner.commands[1] == [
        "gh",
        "api",
        "repos/arthexis/arthexis/commits/head-sha/check-runs?per_page=100&page=1&filter=all",
    ]
    assert runner.commands[2] == [
        "gh",
        "api",
        "repos/arthexis/arthexis/commits/head-sha/check-runs?per_page=100&page=2&filter=all",
    ]


def test_pr_view_keeps_ambiguous_sonar_check_run_keys_blocking():
    details_url = "https://sonarcloud.io/dashboard?id=arthexis_arthexis"
    payload = _pr_payload(
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "SonarCloud Code Analysis",
                "detailsUrl": details_url,
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            }
        ]
    )
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(payload)),
            CommandResult(
                0,
                json.dumps(
                    {
                        "check_runs": [
                            {
                                "name": "SonarCloud Code Analysis",
                                "details_url": details_url,
                                "app": {"name": "SonarQubeCloud"},
                            },
                            {
                                "name": "SonarCloud Code Analysis",
                                "details_url": details_url,
                                "app": {"name": "GitHub Actions"},
                            },
                        ]
                    }
                ),
            ),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.pr_view(123)

    sonar_check = result["statusCheckRollup"][0]
    assert "app" not in sonar_check
    gate = readiness_gate(result)
    assert gate["ready"] is False
    assert gate["blockers"] == ["check:SonarCloud Code Analysis:FAILURE"]


def test_list_open_prs_enriches_sonar_check_run_app_identity():
    details_url = "https://sonarcloud.io/dashboard?id=arthexis_arthexis"
    payload = _pr_payload(
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "SonarCloud Code Analysis",
                "detailsUrl": details_url,
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            }
        ]
    )
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps([payload])),
            CommandResult(
                0,
                json.dumps(
                    {
                        "check_runs": [
                            {
                                "name": "SonarCloud Code Analysis",
                                "details_url": details_url,
                                "app": {"name": "SonarQubeCloud"},
                            }
                        ]
                    }
                ),
            ),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.list_open_prs()

    sonar_check = result[0]["statusCheckRollup"][0]
    assert sonar_check["app"] == {"name": "SonarQubeCloud"}
    assert "headRefOid" in runner.commands[0][-1]


def test_work_profile_affinity_scores_same_role_app_and_hardware():
    profile = infer_work_profile(
        title="Improve Terminal patchwork on Raspberry Pi nodes",
        body="Native local development path.",
        files=["apps/repos/pr_oversee/service.py", "scripts/preflight-env.sh"],
    )

    result = score_node_affinity(
        profile,
        node_role="Terminal",
        installed_apps=("apps.repos",),
        hardware_tags=("raspberry-pi",),
    )

    assert "Terminal" in profile["roles"]
    assert "apps.repos" in profile["apps"]
    assert "raspberry-pi" in profile["hardware"]
    assert result["classification"] == "same-role"
    assert result["score"] > 80


def test_work_profile_infers_ocpp_and_imager_priority_domains():
    profile = infer_work_profile(
        title="Maintain OCPP and imager burn flow",
        body="The GWAY image burn path needs to stay visible in the queue.",
        files=[
            "apps/ocpp/consumers/csms/consumer.py",
            "apps/imager/services/models.py",
        ],
    )

    assert profile["priorityDomains"] == ["imager", "ocpp"]
    assert "priority-domain:imager" in profile["reasons"]
    assert "priority-domain:ocpp" in profile["reasons"]


def test_work_profile_affinity_handles_none_text_and_prefers_role_mismatch():
    profile = infer_work_profile(
        title=None,
        body=None,
        files=["apps/cards/rfid.py"],
    )

    result = score_node_affinity(
        profile,
        node_role="Terminal",
        installed_apps=("apps.cards",),
    )

    assert "Control" in profile["roles"]
    assert result["classification"] == "role-mismatch"


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


def test_readiness_gate_blocks_spoofed_sonarcloud_substring_check():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "name": "my-sonarcloud-spoof",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                },
            ],
        )
    )

    assert result["ready"] is False
    assert result["blockers"] == ["check:my-sonarcloud-spoof:FAILURE"]
    assert result["checks"]["advisory"] == []
    assert result["checks"]["failing"][0]["name"] == "my-sonarcloud-spoof"


def test_readiness_gate_blocks_exact_sonarcloud_label_from_untrusted_app():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "name": "SonarCloud Code Analysis",
                    "app": {"name": "GitHub Actions"},
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                },
            ],
        )
    )

    assert result["ready"] is False
    assert result["blockers"] == ["check:SonarCloud Code Analysis:FAILURE"]
    assert result["checks"]["advisory"] == []
    assert result["checks"]["failing"][0]["name"] == "SonarCloud Code Analysis"


def test_readiness_gate_blocks_exact_sonarcloud_label_without_trusted_source():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "name": "SonarCloud Code Analysis",
                    "detailsUrl": (
                        "https://github.com/arthexis/arthexis/actions/runs/42/job/99"
                    ),
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                },
            ],
        )
    )

    assert result["ready"] is False
    assert result["blockers"] == ["check:SonarCloud Code Analysis:FAILURE"]
    assert result["checks"]["advisory"] == []
    assert result["checks"]["failing"][0]["name"] == "SonarCloud Code Analysis"


def test_readiness_gate_blocks_no_app_check_run_sonarcloud_target_spoof():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "__typename": "CheckRun",
                    "name": "SonarCloud Code Analysis",
                    "detailsUrl": (
                        "https://sonarcloud.io/dashboard"
                        "?id=arthexis_arthexis&pullRequest=9049"
                    ),
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "workflowName": "",
                },
                {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        )
    )

    assert result["ready"] is False
    assert result["blockers"] == ["check:SonarCloud Code Analysis:FAILURE"]
    assert result["checks"]["advisory"] == []
    assert result["checks"]["failing"][0]["name"] == "SonarCloud Code Analysis"


def test_readiness_gate_blocks_status_context_sonarcloud_target_spoof():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "__typename": "StatusContext",
                    "context": "SonarCloud Code Analysis",
                    "targetUrl": (
                        "https://sonarcloud.io/dashboard"
                        "?id=arthexis_arthexis&pullRequest=9049"
                    ),
                    "state": "FAILURE",
                },
            ],
        )
    )

    assert result["ready"] is False
    assert result["blockers"] == ["check:SonarCloud Code Analysis:FAILURE"]
    assert result["checks"]["advisory"] == []
    assert result["checks"]["failing"][0]["name"] == "SonarCloud Code Analysis"


def test_readiness_gate_treats_sonarcloud_as_advisory():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "name": "SonarCloud Code Analysis",
                    "app": {"name": "SonarCloud"},
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                },
                {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        )
    )

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["checks"]["advisory"][0]["name"] == "SonarCloud Code Analysis"
    assert result["checks"]["failing"] == []


def test_readiness_gate_treats_sonarqubecloud_as_advisory():
    result = readiness_gate(
        _pr_payload(
            statusCheckRollup=[
                {
                    "name": "SonarCloud Code Analysis",
                    "app": {"name": "SonarQubeCloud"},
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                },
                {"name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        )
    )

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["checks"]["advisory"][0]["name"] == "SonarCloud Code Analysis"
    assert result["checks"]["failing"] == []


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


def test_review_batch_orders_unresolved_threads_by_severity_and_path():
    threads_payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "thread-p3",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "docs/pr.md",
                                "line": 3,
                                "startLine": None,
                                "comments": {
                                    "nodes": [
                                        {
                                            "id": "comment-p3",
                                            "author": {"login": "reviewer"},
                                            "body": "P3: wording cleanup.",
                                            "createdAt": "2026-06-12T18:01:00Z",
                                            "updatedAt": "2026-06-12T18:01:00Z",
                                            "url": "https://example.test/p3",
                                        }
                                    ]
                                },
                            },
                            {
                                "id": "thread-p1",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "apps/repos/pr_oversee.py",
                                "line": 7,
                                "startLine": None,
                                "comments": {
                                    "nodes": [
                                        {
                                            "id": "comment-p1",
                                            "author": {"login": "reviewer"},
                                            "body": "Security: fail closed here.",
                                            "createdAt": "2026-06-12T18:00:00Z",
                                            "updatedAt": "2026-06-12T18:00:00Z",
                                            "url": "https://example.test/p1",
                                        }
                                    ]
                                },
                            },
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload())),
            CommandResult(0, json.dumps(threads_payload)),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.review_batch(123)

    assert result["severityCounts"] == {"P1": 1, "P3": 1}
    assert [thread["id"] for thread in result["threads"]] == ["thread-p1", "thread-p3"]
    assert result["threads"][0]["summary"] == "Security: fail closed here."


def test_domain_preflight_detects_gway_imager_node_registration_risk():
    pr = _pr_payload(
        title="Reserve GWAY number during image bootstrap",
        files=[
            {"path": "apps/imager/burn.py"},
            {"path": "apps/nodes/registration.py"},
        ],
    )
    runner = FakeRunner([CommandResult(0, json.dumps(pr))])
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.domain_preflight(123)

    assert result["risk"] == "high"
    assert [match["name"] for match in result["matches"]] == [
        "GWAY number reservation",
        "Image burn/bootstrap",
        "Node registration",
    ]
    assert (
        ".venv/bin/python -m pytest apps/imager"
        in result["validationCommands"]
    )
    assert (
        ".venv/bin/python -m pytest apps/nodes"
        in result["validationCommands"]
    )


def test_compact_monitor_result_summarizes_waiting_checks():
    overseer = PullRequestOverseer(repo="arthexis/arthexis")

    result = overseer.compact_monitor_result(
        {
            "repo": "arthexis/arthexis",
            "number": 123,
            "status": "waiting",
            "complete": False,
            "manualDecisionRequired": False,
            "manualDecisionReasons": [],
            "iterationCount": 1,
            "actions": [],
            "last": {
                "gate": {
                    "number": 123,
                    "ready": False,
                    "state": "OPEN",
                    "headRefName": "branch",
                    "headRefOid": "sha",
                    "blockers": ["pending:Tests:IN_PROGRESS"],
                    "checks": {
                        "passing": [],
                        "pending": [{"name": "Tests"}],
                        "failing": [],
                        "superseded": [],
                    },
                }
            },
        }
    )

    assert result["outcome"] == "waiting"
    assert result["pendingChecks"] == ["Tests"]
    assert result["blockers"] == ["pending:Tests:IN_PROGRESS"]


def test_compact_monitor_result_keeps_max_iteration_pending_waits_non_manual():
    overseer = PullRequestOverseer(repo="arthexis/arthexis")

    result = overseer.compact_monitor_result(
        {
            "repo": "arthexis/arthexis",
            "number": 123,
            "status": "manual_decision_required",
            "complete": False,
            "manualDecisionRequired": True,
            "manualDecisionReasons": ["monitor:max_iterations"],
            "iterationCount": 1,
            "actions": [],
            "last": {
                "gate": {
                    "number": 123,
                    "ready": False,
                    "state": "OPEN",
                    "headRefName": "branch",
                    "headRefOid": "sha",
                    "blockers": ["pending:Tests:IN_PROGRESS"],
                    "checks": {
                        "passing": [],
                        "pending": [{"name": "Tests"}],
                        "failing": [],
                        "superseded": [],
                    },
                }
            },
        }
    )

    assert result["outcome"] == "waiting"
    assert result["manualDecisionRequired"] is False
    assert result["manualDecisionReasons"] == []
    assert result["pendingChecks"] == ["Tests"]


def test_compact_monitor_result_reports_manual_before_ready():
    overseer = PullRequestOverseer(repo="arthexis/arthexis")

    result = overseer.compact_monitor_result(
        {
            "repo": "arthexis/arthexis",
            "number": 123,
            "status": "manual",
            "complete": False,
            "manualDecisionRequired": True,
            "manualDecisionReasons": ["local_validation_failed"],
            "iterationCount": 1,
            "actions": [],
            "last": {
                "gate": {
                    "number": 123,
                    "ready": True,
                    "state": "OPEN",
                    "headRefName": "branch",
                    "headRefOid": "sha",
                    "blockers": [],
                    "checks": {"passing": [], "pending": [], "failing": []},
                }
            },
        }
    )

    assert result["outcome"] == "manual"
    assert result["ready"] is True
    assert result["manualDecisionReasons"] == ["local_validation_failed"]


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
    assert result["workflowChange"] is True
    assert result["affectedInstallShard"] == "rest"
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
        "scripts/check_migration_conflicts.py",
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
    assert result["focusedValidation"]["scope"] == "focused-pr"
    assert result["mainReleaseValidation"] == {
        "scope": "main-release",
        "unchanged": True,
        "githubActions": [
            {
                "name": "Full release/main validation",
                "scope": "main-release",
                "selection": "unchanged",
                "reason": "Release publish and main-branch gates continue to run their full validation.",
            }
        ],
    }


def test_test_plan_does_not_treat_service_models_as_schema_changes():
    result = changed_files_to_test_plan(["apps/imager/services/models.py"])

    assert result["apps"] == ["imager"]
    assert result["modelChange"] is False
    assert [
        sys.executable,
        "manage.py",
        "makemigrations",
        "--check",
        "--dry-run",
    ] not in result["commands"]


def test_test_plan_selects_ocpp_and_combined_install_shards():
    assert affected_install_shard(["apps/ocpp/consumers.py"]) == "ocpp"
    assert affected_install_shard([".importlinter"]) == "rest"
    assert affected_install_shard(["apps/repos/pr_oversee/service.py"]) == "rest"
    assert (
        affected_install_shard(
            ["apps/ocpp/consumers.py", "apps/repos/pr_oversee/service.py"]
        )
        == "both"
    )
    assert affected_install_shard(["docs/development/usage.md"]) == "none"


def test_test_plan_renders_markdown_with_validation_scopes():
    plan = changed_files_to_test_plan(
        ["apps/repos/pr_oversee/hygiene.py", ".github/workflows/install-health.yml"]
    )

    markdown = render_test_plan_markdown(plan)

    assert "# Affected Validation Plan" in markdown
    assert "## Focused PR Validation" in markdown
    assert "## Main/Release Validation" in markdown
    assert "- Affected install shard: `rest`" in markdown
    assert "Full release/main validation" in markdown


def test_overseer_local_test_plan_uses_git_diff_against_base_ref():
    runner = FakeRunner(
        [
            CommandResult(0, "apps/repos/pr_oversee/hygiene.py\n"),
            CommandResult(0, "apps/repos/pr_oversee/service.py\n"),
            CommandResult(0, "apps/repos/pr_oversee/hygiene.py\n"),
            CommandResult(0, "apps/repos/new_file.py\n.arthexis-pr-oversee.json\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    plan = overseer.local_test_plan(base_ref="origin/main")

    assert plan["source"] == "local-diff"
    assert plan["baseRef"] == "origin/main"
    assert plan["files"] == [
        "apps/repos/new_file.py",
        "apps/repos/pr_oversee/hygiene.py",
        "apps/repos/pr_oversee/service.py",
    ]
    assert runner.commands == [
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]


def test_overseer_local_test_plan_falls_back_when_origin_main_is_missing():
    runner = FakeRunner(
        [
            CommandResult(128, "", "fatal: ambiguous argument 'origin/main...HEAD'"),
            CommandResult(0, "apps/repos/from-main.py\n"),
            CommandResult(0, ""),
            CommandResult(0, ""),
            CommandResult(0, ""),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    plan = overseer.local_test_plan()

    assert plan["files"] == ["apps/repos/from-main.py"]
    assert runner.commands == [
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "diff", "--name-only", "main...HEAD"],
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]


def test_overseer_local_test_plan_fails_when_default_base_refs_are_missing():
    runner = FakeRunner(
        [
            CommandResult(128, "", "fatal: ambiguous argument 'origin/main...HEAD'"),
            CommandResult(128, "", "fatal: ambiguous argument 'main...HEAD'"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    with pytest.raises(PullRequestOverseeError, match="main\\.\\.\\.HEAD"):
        overseer.local_test_plan()

    assert runner.commands == [
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "diff", "--name-only", "main...HEAD"],
    ]


@pytest.mark.parametrize(
    "selector_args",
    [
        ["--pr", "123", "--local"],
        ["--pr", "123", "--changed-file", "apps/repos/pr_oversee/hygiene.py"],
        ["--local", "--changed-file", "apps/repos/pr_oversee/hygiene.py"],
    ],
)
def test_management_command_test_plan_rejects_conflicting_selectors(selector_args):
    with pytest.raises(CommandError, match="Choose only one selector for test-plan"):
        call_command(
            "pr_oversee",
            "--repo",
            "arthexis/arthexis",
            "test-plan",
            *selector_args,
        )


def test_management_command_database_fallback_is_limited_to_test_plan(monkeypatch):
    command = PrOverseeCommand()
    monkeypatch.setattr(
        "apps.repos.management.commands.pr_oversee.resolve_active_repository",
        Mock(side_effect=DatabaseError("database is locked")),
    )

    assert (
        command._resolve_repository("", allow_database_fallback=True)
        == "arthexis/arthexis"
    )
    with pytest.raises(DatabaseError, match="database is locked"):
        command._resolve_repository("", allow_database_fallback=False)


def test_management_command_test_plan_pr_requires_repo_when_database_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        "apps.repos.management.commands.pr_oversee.resolve_active_repository",
        Mock(side_effect=DatabaseError("database is locked")),
    )

    with pytest.raises(DatabaseError, match="database is locked"):
        call_command("pr_oversee", "test-plan", "--pr", "123")


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


def test_closed_pr_report_filters_recent_closed_prs_and_counts_states():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "number": 1,
                            "title": "Recent merge",
                            "url": "https://example.test/pull/1",
                            "state": "MERGED",
                            "author": {"login": "codex"},
                            "closedAt": "2026-06-12T23:30:00Z",
                            "mergedAt": "2026-06-12T23:30:00Z",
                            "headRefName": "recent-merge",
                            "baseRefName": "main",
                        },
                        {
                            "number": 2,
                            "title": "Recent close",
                            "url": "https://example.test/pull/2",
                            "state": "CLOSED",
                            "author": {"login": "codex"},
                            "closedAt": "2026-06-12T22:00:00Z",
                            "mergedAt": None,
                            "headRefName": "recent-close",
                            "baseRefName": "main",
                        },
                        {
                            "number": 3,
                            "title": "Old merge",
                            "url": "https://example.test/pull/3",
                            "state": "MERGED",
                            "author": {"login": "codex"},
                            "closedAt": "2026-06-12T12:00:00Z",
                            "mergedAt": "2026-06-12T12:00:00Z",
                            "headRefName": "old-merge",
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.closed_pr_report(
        since_hours=8,
        now=datetime(2026, 6, 13, 1, 0, tzinfo=timezone.utc),
    )

    assert result["cutoff"] == "2026-06-12T17:00:00Z"
    assert result["closedCount"] == 2
    assert result["mergedCount"] == 1
    assert result["closedUnmergedCount"] == 1
    assert [item["number"] for item in result["items"]] == [1, 2]
    assert runner.commands[0] == [
        "gh",
        "pr",
        "list",
        "--repo",
        "arthexis/arthexis",
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        "number,title,url,state,author,closedAt,mergedAt,headRefName,baseRefName",
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


def test_malformed_dependency_title_without_target_version_is_ignored():
    pr = {
        "number": 1,
        "title": "Bump django from 5.2 to ",
        "author": {"login": "dependabot[bot]"},
        "headRefName": "dependabot/pip/django",
        "updatedAt": "2026-05-01T00:00:00Z",
    }

    assert pr_oversee.dependency_key(pr) == "django"
    assert pr_oversee.dependency_target_version(pr) == ""


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


def test_dependency_title_parser_ignores_empty_to_version():
    result = dependency_duplicates(
        [
            {
                "number": 1,
                "title": "Bump django from 5.2.11 to ",
                "author": {"login": "dependabot[bot]"},
                "headRefName": "dependabot/pip/django",
                "updatedAt": "2026-05-01T00:00:00Z",
            }
        ]
    )

    assert result == {}


def test_parse_pr_dependency_edges_uses_default_repo_and_rejects_duplicates():
    assert parse_pr_dependency_edges(
        ["#9106=#9105", "arthexis/gway-ap-kiosk#1=arthexis/arthexis#9104"],
        default_repo="arthexis/arthexis",
    ) == {
        "arthexis/arthexis#9106": ("arthexis/arthexis#9105",),
        "arthexis/gway-ap-kiosk#1": ("arthexis/arthexis#9104",),
    }

    with pytest.raises(PullRequestOverseeError, match="duplicated"):
        parse_pr_dependency_edges(
            ["#9106=#9105", "#9106=#9104"], default_repo="arthexis/arthexis"
        )


def test_dependency_graph_orders_prerequisites_and_blocks_unmerged_dependencies():
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload(number=9104, reviewDecision=""))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, json.dumps(_pr_payload(number=9105, state="MERGED"))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, json.dumps(_pr_payload(number=9106, reviewDecision=""))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, json.dumps(_pr_payload(number=1, title="Kiosk", reviewDecision=""))),
            CommandResult(0, json.dumps(_review_threads_payload())),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.dependency_graph(
        dependencies=[
            "#9106=#9105",
            "arthexis/gway-ap-kiosk#1=#9104",
        ]
    )

    assert result["order"] == [
        "arthexis/arthexis#9104",
        "arthexis/arthexis#9105",
        "arthexis/arthexis#9106",
        "arthexis/gway-ap-kiosk#1",
    ]
    items = {item["pr"]: item for item in result["items"]}
    assert items["arthexis/arthexis#9105"]["status"] == "merged"
    assert items["arthexis/arthexis#9106"]["status"] == "ready-to-merge"
    assert items["arthexis/gway-ap-kiosk#1"]["status"] == "blocked-by-dependency"
    assert items["arthexis/gway-ap-kiosk#1"]["blockedBy"] == [
        "arthexis/arthexis#9104"
    ]
    assert result["nextActions"] == [
        {"pr": "arthexis/arthexis#9104", "action": "merge"},
        {"pr": "arthexis/arthexis#9106", "action": "merge"},
    ]
    pr_view_commands = [command for command in runner.commands if command[1:3] == ["pr", "view"]]
    assert pr_view_commands[3][:6] == [
        "gh",
        "pr",
        "view",
        "1",
        "--repo",
        "arthexis/gway-ap-kiosk",
    ]


def test_dependency_graph_blocks_unresolved_review_threads():
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload(number=9103, state="MERGED"))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, json.dumps(_pr_payload(number=9104, reviewDecision=""))),
            CommandResult(0, json.dumps(_review_threads_payload(unresolved=True))),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.dependency_graph(dependencies=["#9104=#9103"])

    item = next(item for item in result["items"] if item["pr"].endswith("#9104"))
    assert item["status"] == "awaiting-pr-gate"
    assert item["gate"]["blockers"] == ["review_threads:UNRESOLVED:1"]


def test_dependency_graph_distinguishes_cycle_members_from_downstream_prs():
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload(number=1, reviewDecision=""))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, json.dumps(_pr_payload(number=2, reviewDecision=""))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, json.dumps(_pr_payload(number=3, reviewDecision=""))),
            CommandResult(0, json.dumps(_review_threads_payload())),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.dependency_graph(
        dependencies=["#1=#2", "#2=#1", "#3=#1"]
    )

    assert result["cycles"] == ["arthexis/arthexis#1", "arthexis/arthexis#2"]
    items = {item["pr"]: item for item in result["items"]}
    assert items["arthexis/arthexis#3"]["status"] == "blocked-by-dependency"
    assert items["arthexis/arthexis#3"]["blockedBy"] == ["arthexis/arthexis#1"]


def test_dependency_graph_enriches_advisory_checks_from_the_pr_repository():
    check_url = "https://sonarcloud.io/dashboard?id=arthexis_gway-ap-kiosk&pullRequest=1"
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload(number=9104, state="MERGED"))),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=1,
                        headRefOid="kiosk-head",
                        statusCheckRollup=[
                            {
                                "__typename": "CheckRun",
                                "name": "SonarCloud Code Analysis",
                                "status": "COMPLETED",
                                "conclusion": "FAILURE",
                                "detailsUrl": check_url,
                            }
                        ],
                    )
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    {
                        "check_runs": [
                            {
                                "name": "SonarCloud Code Analysis",
                                "details_url": check_url,
                                "app": {"name": "SonarQubeCloud"},
                            }
                        ]
                    }
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.dependency_graph(
        dependencies=["arthexis/gway-ap-kiosk#1=#9104"]
    )

    item = next(item for item in result["items"] if item["pr"].endswith("#1"))
    assert item["readyToMerge"] is True
    check_run_command = next(
        command
        for command in runner.commands
        if command[1:3] == ["api", "repos/arthexis/gway-ap-kiosk/commits/kiosk-head/check-runs?per_page=100&page=1&filter=all"]
    )
    assert check_run_command[0] == "gh"


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


def test_advance_prioritizes_ocpp_before_imager_domains_within_gate_state():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "number": 123,
                            "title": "Generic cleanup",
                            "isDraft": False,
                        },
                        {
                            "number": 124,
                            "title": "Maintain generic desktop shortcuts",
                            "isDraft": False,
                        },
                        {
                            "number": 125,
                            "title": "Improve GWAY imager burn flow",
                            "isDraft": False,
                        },
                        {
                            "number": 126,
                            "title": "Fix OCPP charger session handling",
                            "isDraft": False,
                        },
                    ]
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=123,
                        title="Generic cleanup",
                        updatedAt="2026-05-01T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "docs/reference/cleanup.md\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=124,
                        title="Maintain generic desktop shortcuts",
                        updatedAt="2026-05-02T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/core/shortcuts.py\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=125,
                        title="Improve GWAY imager burn flow",
                        updatedAt="2026-05-03T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/imager/services/models.py\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=126,
                        title="Fix OCPP charger session handling",
                        updatedAt="2026-05-04T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/ocpp/consumers/csms/consumer.py\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.advance(limit=4)

    assert [item["number"] for item in result["topSuggestions"]] == [126, 125, 123]
    assert result["topSuggestions"][0]["priorityDomains"] == ["ocpp"]
    assert result["topSuggestions"][0]["operatorPriority"] == 0
    assert result["topSuggestions"][0]["domainPriority"] == 0
    assert result["topSuggestions"][1]["domainPriority"] == 1
    assert result["topSuggestions"][2]["domainPriority"] == 2
    assert result["items"][3]["domainPriority"] == 2


def test_advance_ocpp_review_work_preempts_generic_ready_pr():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "number": 123,
                            "title": "Generic cleanup",
                            "isDraft": False,
                        },
                        {
                            "number": 124,
                            "title": "Fix OCPP charger session handling",
                            "isDraft": False,
                        },
                    ]
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=123,
                        title="Generic cleanup",
                        updatedAt="2026-05-02T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "docs/reference/cleanup.md\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=124,
                        title="Fix OCPP charger session handling",
                        reviewDecision="CHANGES_REQUESTED",
                        updatedAt="2026-05-01T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/ocpp/consumers/csms/consumer.py\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.advance(limit=2)

    assert [item["number"] for item in result["topSuggestions"]] == [124, 123]
    assert result["topSuggestions"][0]["operatorPriority"] == 0
    assert (
        result["topSuggestions"][0]["priority"]
        > result["topSuggestions"][1]["priority"]
    )


def test_advance_does_not_prioritize_ocpp_draft_over_ready_pr():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "number": 123,
                            "title": "Generic cleanup",
                            "isDraft": False,
                        },
                        {
                            "number": 124,
                            "title": "Fix OCPP charger session handling",
                            "isDraft": True,
                        },
                    ]
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=123,
                        title="Generic cleanup",
                        updatedAt="2026-05-02T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "docs/reference/cleanup.md\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=124,
                        title="Fix OCPP charger session handling",
                        isDraft=True,
                        updatedAt="2026-05-01T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/ocpp/consumers/csms/consumer.py\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.advance(limit=2, include_drafts=True)

    assert [item["number"] for item in result["topSuggestions"]] == [123, 124]
    assert result["topSuggestions"][0]["readyToMerge"] is True
    assert result["topSuggestions"][1]["canMarkReady"] is True
    assert result["topSuggestions"][1]["priorityDomains"] == ["ocpp"]
    assert result["topSuggestions"][1]["operatorPriority"] == 1
    assert result["topSuggestions"][1]["domainPriority"] == 0


def test_advance_executes_write_actions_in_prioritized_order():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "number": 123,
                            "title": "Generic cleanup",
                            "isDraft": False,
                        },
                        {
                            "number": 124,
                            "title": "Maintain generic desktop shortcuts",
                            "isDraft": False,
                        },
                        {
                            "number": 125,
                            "title": "Improve GWAY imager burn flow",
                            "isDraft": False,
                        },
                        {
                            "number": 126,
                            "title": "Fix OCPP charger session handling",
                            "isDraft": False,
                        },
                    ]
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=123,
                        title="Generic cleanup",
                        updatedAt="2026-05-01T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "docs/reference/cleanup.md\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=124,
                        title="Maintain generic desktop shortcuts",
                        updatedAt="2026-05-02T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/core/shortcuts.py\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=125,
                        title="Improve GWAY imager burn flow",
                        updatedAt="2026-05-03T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/imager/services/models.py\n"),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=126,
                        title="Fix OCPP charger session handling",
                        updatedAt="2026-05-04T00:00:00Z",
                    )
                ),
            ),
            CommandResult(0, json.dumps(_review_threads_payload())),
            CommandResult(0, "apps/ocpp/consumers/csms/consumer.py\n"),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)
    overseer.execute_actions = Mock(return_value=[])

    result = overseer.advance(limit=4, merge=True, write=True)

    [action_plans] = overseer.execute_actions.call_args.args
    assert [item["number"] for item in result["topSuggestions"]] == [126, 125, 123]
    assert [action["number"] for action in action_plans] == [126, 125, 123, 124]


def test_node_queue_ranks_prs_by_local_role_app_and_hardware_fit():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {"number": 123, "title": "Terminal tooling", "isDraft": False},
                        {"number": 124, "title": "RFID scanner", "isDraft": False},
                    ]
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=123,
                        title="Improve Terminal patchwork on Raspberry Pi nodes",
                        body="Native PR work through patchwork.",
                        updatedAt="2026-05-01T00:00:00Z",
                        files=[{"path": "apps/repos/pr_oversee/service.py"}],
                    )
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=124,
                        title="Improve Control RFID scanner flow",
                        body="Control node RFID hardware.",
                        updatedAt="2026-05-01T00:00:01Z",
                        files=[{"path": "apps/cards/rfid.py"}],
                    )
                ),
            ),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.node_queue(
        limit=2,
        node_role="Terminal",
        installed_apps=("apps.repos",),
        hardware_tags=("raspberry-pi",),
        local_development={"allowed": True},
    )

    assert [item["number"] for item in result["topSuggestions"]] == [123, 124]
    assert result["topSuggestions"][0]["nodeAffinity"]["classification"] == "same-role"
    assert (
        result["topSuggestions"][0]["nodeAffinity"]["score"]
        > result["topSuggestions"][1]["nodeAffinity"]["score"]
    )
    assert result["nodeContext"]["localDevelopment"] == {"allowed": True}
    assert not any("diff" in command for command in runner.commands)


def test_node_queue_prefers_newer_pr_when_affinity_scores_tie():
    runner = FakeRunner(
        [
            CommandResult(
                0,
                json.dumps(
                    [
                        {"number": 123, "title": "Docs cleanup", "isDraft": False},
                        {"number": 124, "title": "Docs cleanup", "isDraft": False},
                    ]
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=123,
                        title="Docs cleanup",
                        body="",
                        updatedAt="2026-05-01T00:00:00Z",
                        files=[{"path": "docs/reference/old.md"}],
                    )
                ),
            ),
            CommandResult(
                0,
                json.dumps(
                    _pr_payload(
                        number=124,
                        title="Docs cleanup",
                        body="",
                        updatedAt="2026-05-02T00:00:00Z",
                        files=[{"path": "docs/reference/new.md"}],
                    )
                ),
            ),
        ]
    )
    overseer = PullRequestOverseer(repo="arthexis/arthexis", runner=runner)

    result = overseer.node_queue(limit=2, node_role="Terminal")

    assert [item["number"] for item in result["topSuggestions"]] == [124, 123]
    assert (
        result["topSuggestions"][0]["nodeAffinity"]["score"]
        == result["topSuggestions"][1]["nodeAffinity"]["score"]
    )


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


def test_path_is_relative_to_excludes_external_symlink(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    link = root / "link"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    assert _path_is_relative_to(root / "child", root) is True
    assert _path_is_relative_to(link, root) is False


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


def test_patchwork_status_scan_does_not_expand_untracked_venv(tmp_path: Path):
    worktree = tmp_path / "patchwork" / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    runner = FakeRunner([CommandResult(0)])
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    assert overseer._worktree_status_lines(worktree) == []

    status_command = runner.commands[0]
    assert "--untracked-files=normal" in status_command
    assert "--untracked-files=all" not in status_command


def test_cleanup_forces_removal_for_owned_patchwork_metadata(tmp_path: Path):
    worktree = tmp_path / "patchwork" / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    (worktree / ".arthexis-pr-oversee.json").write_text('{"number": 123}\n')
    runner = FakeRunner(
        [
            CommandResult(0, json.dumps(_pr_payload(state="MERGED"))),
            CommandResult(0, "?? .arthexis-pr-oversee.json\n?? .venv/\n"),
            CommandResult(128, stderr="contains modified or untracked files"),
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


def test_patchwork_remove_unlinks_owned_venv_before_git_remove(tmp_path: Path):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    venv_source = tmp_path / "shared-venv"
    (venv_source / "Scripts").mkdir(parents=True)
    (venv_source / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    venv = _local_venv_link(venv_source, worktree / ".venv")
    assert venv["linked"] is True
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"number": 123, "venv": venv})
    )
    runner = FakeRunner([CommandResult(0)])
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["preRemove"] == {
        "attempted": True,
        "removed": True,
        "path": ".venv",
    }
    assert not worktree.exists()
    assert (venv_source / "Scripts" / "python.exe").exists()


def test_patchwork_remove_blocks_dirty_worktree_before_unlinking_venv(
    tmp_path: Path,
):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    venv_source = tmp_path / "shared-venv"
    venv_source.mkdir()
    venv = _local_venv_link(venv_source, worktree / ".venv")
    assert venv["linked"] is True
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"number": 123, "venv": venv})
    )
    runner = FakeRunner([CommandResult(0, "?? real-change.txt\n")])
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["blocked"] is True
    assert result["status"] == ["?? real-change.txt"]
    assert all(
        command[:3] != ["git", "worktree", "remove"] for command in runner.commands
    )
    assert (worktree / ".venv").exists()
    assert venv_source.exists()


def test_patchwork_remove_blocks_venv_link_without_restorable_metadata(
    tmp_path: Path,
):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    venv_source = tmp_path / "shared-venv"
    venv_source.mkdir()
    venv = _local_venv_link(venv_source, worktree / ".venv")
    assert venv["linked"] is True
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"number": 123, "venv": {"linked": True}})
    )
    runner = FakeRunner(
        [
            CommandResult(0, "?? .arthexis-pr-oversee.json\n?? .venv/\n"),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["blocked"] is True
    assert result["preRemove"]["reason"] == "metadata-not-restorable"
    assert all(
        command[:3] != ["git", "worktree", "remove"] for command in runner.commands
    )
    assert (worktree / ".venv").exists()
    assert venv_source.exists()


def test_patchwork_remove_aborts_when_owned_venv_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    venv_source = tmp_path / "shared-venv"
    venv_source.mkdir()
    venv = _local_venv_link(venv_source, worktree / ".venv")
    assert venv["linked"] is True
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"number": 123, "venv": venv})
    )
    monkeypatch.setattr(
        "apps.repos.pr_oversee.worktree._remove_owned_path",
        Mock(side_effect=OSError("locked")),
    )
    runner = FakeRunner()
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["blocked"] is True
    assert result["preRemove"]["removed"] is False
    assert "locked" in result["preRemove"]["error"]
    assert all(
        command[:3] != ["git", "worktree", "remove"] for command in runner.commands
    )
    assert worktree.exists()
    assert venv_source.exists()


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
            CommandResult(0, "?? .arthexis-pr-oversee.json\n"),
            CommandResult(128, stderr="contains modified or untracked files"),
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


def test_patchwork_remove_restores_venv_when_git_removal_fails(tmp_path: Path):
    patchwork_root = tmp_path / "patchwork"
    worktree = patchwork_root / "arthexis-arthexis-pr-123"
    worktree.mkdir(parents=True)
    venv_source = tmp_path / "shared-venv"
    venv_source.mkdir()
    venv = _local_venv_link(venv_source, worktree / ".venv")
    assert venv["linked"] is True
    (worktree / ".arthexis-pr-oversee.json").write_text(
        json.dumps({"number": 123, "venv": venv})
    )
    runner = FakeRunner(
        [
            CommandResult(0, "?? .arthexis-pr-oversee.json\n?? .venv/\n"),
            CommandResult(128, stderr="contains modified or untracked files"),
            CommandResult(128, stderr="worktree is locked"),
        ]
    )
    overseer = PullRequestOverseer(
        repo="arthexis/arthexis", runner=runner, cwd=tmp_path
    )

    result = overseer._remove_worktree(worktree, patchwork_root=patchwork_root)

    assert result["forced"] is True
    assert result["forceReturncode"] == 128
    assert result["venvRestore"]["restored"] is True
    assert worktree.exists()
    assert (worktree / ".venv").exists()
    assert venv_source.exists()


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


def test_hygiene_allows_validated_model_change_without_migration():
    result = hygiene_report(
        _pr_payload(
            body=(
                "## Summary\n"
                "Closes #123\n\n"
                "## Validation\n"
                "- `.venv/bin/python manage.py makemigrations --check --dry-run` "
                "(No changes detected)"
            )
        ),
        ["apps/users/models/user.py"],
    )

    assert result["ok"] is True
    assert "model-change:missing-migration" not in result["failures"]
    assert "model-change:no-migration-validated" in result["warnings"]


def test_hygiene_ignores_service_models_without_migration():
    result = hygiene_report(
        _pr_payload(body="## Summary\nCloses #123\n\n## Validation\n- pytest"),
        ["apps/imager/services/models.py"],
    )

    assert result["ok"] is True
    assert "model-change:missing-migration" not in result["failures"]


def test_review_reply_summary_formats_change_and_validation_body():
    result = review_reply_summary(
        commit="0123456789abcdef",
        changes=["Linked patchwork .venv"],
        validations=["python -m pytest apps/repos/tests"],
    )

    assert result["commit"] == "0123456789ab"
    assert "Addressed in 0123456789ab." in result["body"]
    assert "- Linked patchwork .venv" in result["body"]
    assert "- python -m pytest apps/repos/tests" in result["body"]


def test_review_reply_summary_feedback_issue_keeps_short_body():
    result = review_reply_summary(
        commit="0123456789abcdef",
        changes=["Linked patchwork .venv"],
        validations=["python -m pytest apps/repos/tests"],
        notes=["No further updates expected."],
        feedback_issue=True,
    )

    assert result["commit"] == "0123456789ab"
    assert result["body"] == "Addressed in 0123456789ab.\n"


def test_management_command_test_plan_accepts_changed_files_and_markdown():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    plan = changed_files_to_test_plan(["apps/repos/pr_oversee/hygiene.py"])
    plan["source"] = "changed-files"
    fake.test_plan_for_files = Mock(return_value=plan)
    buffer = StringIO()

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        call_command(
            "pr_oversee",
            "--repo",
            "arthexis/arthexis",
            "test-plan",
            "--changed-file",
            "apps/repos/pr_oversee/hygiene.py",
            "--format",
            "markdown",
            stdout=buffer,
        )

    assert "# Affected Validation Plan" in buffer.getvalue()
    assert "## Focused PR Validation" in buffer.getvalue()
    fake.test_plan_for_files.assert_called_once_with(
        ["apps/repos/pr_oversee/hygiene.py"], source="changed-files"
    )


def test_management_command_test_plan_changed_files_tolerates_unmigrated_repo_db():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    plan = changed_files_to_test_plan(["apps/repos/pr_oversee/hygiene.py"])
    plan["source"] = "changed-files"
    fake.test_plan_for_files = Mock(return_value=plan)
    buffer = StringIO()

    with (
        patch(
            "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
            return_value=fake,
        ) as overseer_class,
        patch(
            "apps.repos.management.commands.pr_oversee.resolve_active_repository",
            side_effect=DatabaseError("no such table: release_package"),
        ),
    ):
        call_command(
            "pr_oversee",
            "--json",
            "test-plan",
            "--changed-file",
            "apps/repos/pr_oversee/hygiene.py",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["source"] == "changed-files"
    overseer_class.assert_called_once_with(repo="arthexis/arthexis")
    fake.test_plan_for_files.assert_called_once_with(
        ["apps/repos/pr_oversee/hygiene.py"], source="changed-files"
    )


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


def test_management_command_closed_report_passes_since_and_limit():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.closed_pr_report = Mock(
        return_value={
            "repo": "arthexis/arthexis",
            "sinceHours": 8.0,
            "closedCount": 0,
            "items": [],
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
            "closed-report",
            "--since-hours",
            "8",
            "--limit",
            "20",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["sinceHours"] == 8.0
    fake.closed_pr_report.assert_called_once_with(since_hours=8.0, limit=20)


def test_management_command_report_closed_accepts_duration_string():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.closed_pr_report = Mock(
        return_value={
            "repo": "arthexis/arthexis",
            "sinceHours": 48.0,
            "closedCount": 0,
            "items": [],
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
            "report",
            "closed",
            "--since",
            "2d",
            "--limit",
            "20",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["sinceHours"] == 48.0
    fake.closed_pr_report.assert_called_once_with(since_hours=48.0, limit=20)


def test_management_command_report_closed_accepts_minutes_duration():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.closed_pr_report = Mock(
        return_value={
            "repo": "arthexis/arthexis",
            "sinceHours": 0.5,
            "closedCount": 0,
            "items": [],
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
            "report",
            "closed",
            "--since",
            "30m",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["sinceHours"] == 0.5
    fake.closed_pr_report.assert_called_once_with(since_hours=0.5, limit=100)


def test_management_command_report_closed_rejects_invalid_duration():
    fake = PullRequestOverseer(repo="arthexis/arthexis")

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError, match="--since must look like"):
            call_command(
                "pr_oversee",
                "--repo",
                "arthexis/arthexis",
                "report",
                "closed",
                "--since",
                ".*h",
            )


def test_management_command_review_batch_can_render_markdown():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.review_batch = Mock(
        return_value={
            "repo": "arthexis/arthexis",
            "number": 123,
            "title": "Review me",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "branch",
            "headRefOid": "abcdef1234567890",
            "unresolvedCount": 1,
            "severityCounts": {"P2": 1},
            "threads": [
                {
                    "severity": "P2",
                    "path": "apps/repos/pr_oversee.py",
                    "line": 10,
                    "isResolved": False,
                    "isOutdated": False,
                    "author": "reviewer",
                    "summary": "Bug fix requested.",
                }
            ],
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
            "review-batch",
            "--pr",
            "123",
            "--format",
            "markdown",
            stdout=buffer,
        )

    assert "# PR #123 Review Batch" in buffer.getvalue()
    assert "`apps/repos/pr_oversee.py:10`" in buffer.getvalue()
    fake.review_batch.assert_called_once_with(123, include_resolved=False)


def test_management_command_domain_preflight_passes_pr_number():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.domain_preflight = Mock(
        return_value={
            "repo": "arthexis/arthexis",
            "number": 123,
            "title": "GWAY",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "branch",
            "headRefOid": "abcdef1234567890",
            "risk": "focused",
            "matches": [],
            "validationCommands": [],
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
            "domain-preflight",
            "--pr",
            "123",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["risk"] == "focused"
    fake.domain_preflight.assert_called_once_with(123)


def test_management_command_checkout_defaults_to_patchwork_dir(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Terminal")
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


def test_management_command_checkout_blocks_non_terminal_role(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Control")
    monkeypatch.setattr(
        "apps.repos.management.commands.pr_oversee.Node.get_local",
        lambda: None,
    )
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.checkout = Mock(return_value={"number": 123})

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError, match="Local PR development is disabled"):
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
            )

    fake.checkout.assert_not_called()


def test_management_command_checkout_allows_authorized_satellite_dev_env(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Satellite")
    monkeypatch.setenv("ARTHEXIS_NODE_DEV_ENV", "1")
    control_assignment_lookup = Mock(
        side_effect=AssertionError("non-Control checkout must not query assignments")
    )
    monkeypatch.setattr(
        PrOverseeCommand,
        "_control_patchwork_assignment_authorized",
        control_assignment_lookup,
    )
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
    control_assignment_lookup.assert_not_called()


def test_management_command_checkout_blocks_control_dev_env_without_assignment(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Control")
    monkeypatch.setenv("ARTHEXIS_NODE_DEV_ENV", "1")
    monkeypatch.setattr(
        "apps.repos.management.commands.pr_oversee.Node.get_local",
        lambda: None,
    )
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.checkout = Mock(return_value={"number": 123})

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError, match="active operator patchwork assignment"):
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
            )

    fake.checkout.assert_not_called()


@pytest.mark.django_db
def test_management_command_checkout_blocks_unmarked_control_patchwork_assignment(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Control")
    monkeypatch.setenv("ARTHEXIS_NODE_DEV_ENV", "1")
    role, _created = NodeRole.objects.get_or_create(
        name="Control",
        defaults={"acronym": "CTRL"},
    )
    node = Node.objects.create(
        hostname="gway-001",
        public_endpoint="gway-001",
        role=role,
    )
    repository = GitHubRepository.objects.create(owner="arthexis", name="arthexis")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=123,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )
    monkeypatch.setattr(
        "apps.repos.management.commands.pr_oversee.Node.get_local",
        lambda: node,
    )
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.checkout = Mock(return_value={"number": 123})

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError, match="active operator patchwork assignment"):
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
            )

    fake.checkout.assert_not_called()


@pytest.mark.django_db
def test_management_command_checkout_allows_control_operator_patchwork_assignment(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Control")
    role, _created = NodeRole.objects.get_or_create(
        name="Control",
        defaults={"acronym": "CTRL"},
    )
    node = Node.objects.create(
        hostname="gway-001",
        public_endpoint="gway-001",
        role=role,
    )
    repository = GitHubRepository.objects.create(owner="arthexis", name="arthexis")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=123,
        node=node,
        patchwork_authorized=True,
        reason=work_assignments.control_manual_patchwork_reason("Operator approved."),
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )
    monkeypatch.setattr(
        "apps.repos.management.commands.pr_oversee.Node.get_local",
        lambda: node,
    )
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


def test_management_command_checkout_blocks_watchtower_dev_env_by_default(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Watchtower")
    monkeypatch.setenv("ARTHEXIS_NODE_DEV_ENV", "1")
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.checkout = Mock(return_value={"number": 123})

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError, match="Watchtower"):
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
            )

    fake.checkout.assert_not_called()


def test_management_command_checkout_requires_existing_patchwork_dir(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Terminal")
    missing_patchwork = tmp_path / "missing"
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.checkout = Mock(return_value={"number": 123})

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError, match="existing assigned patchwork"):
            call_command(
                "pr_oversee",
                "--repo",
                "arthexis/arthexis",
                "--json",
                "checkout",
                "--pr",
                "123",
                "--patchwork-dir",
                str(missing_patchwork),
            )

    fake.checkout.assert_not_called()


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


def test_management_command_node_queue_passes_node_context(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NODE_ROLE", "Satellite")
    monkeypatch.setenv("ARTHEXIS_NODE_DEV_ENV", "1")
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.node_queue = Mock(
        return_value={
            "repo": "arthexis/arthexis",
            "nodeContext": {
                "role": "Satellite",
                "localDevelopment": {"allowed": True},
            },
            "items": [],
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
            "node-queue",
            "--role",
            "Satellite",
            "--installed-app",
            "apps.nmcli",
            "--hardware-tag",
            "network",
            "--patchwork-dir",
            str(tmp_path),
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["nodeContext"]["role"] == "Satellite"
    fake.node_queue.assert_called_once()
    _, kwargs = fake.node_queue.call_args
    assert kwargs["node_role"] == "Satellite"
    assert kwargs["installed_apps"] == ["apps.nmcli"]
    assert kwargs["hardware_tags"] == ["network"]
    assert kwargs["local_development"]["allowed"] is True
    assert set(kwargs["local_development"]) == {
        "allowed",
        "role",
        "patchworkDir",
        "patchworkDirExists",
        "authorized",
        "authorization",
        "roleAllowed",
        "reasons",
    }


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


def test_management_command_monitor_compact_summarizes_payload():
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    monitor_payload = {
        "repo": "arthexis/arthexis",
        "number": 123,
        "status": "waiting",
        "complete": False,
        "manualDecisionRequired": False,
        "manualDecisionReasons": [],
        "iterationCount": 1,
        "actions": [],
        "last": {
            "gate": {
                "number": 123,
                "ready": False,
                "state": "OPEN",
                "blockers": ["pending:Tests:IN_PROGRESS"],
                "checks": {
                    "passing": [],
                    "pending": [{"name": "Tests"}],
                    "failing": [],
                    "superseded": [],
                },
            }
        },
    }
    fake.monitor = Mock(return_value=monitor_payload)
    fake.compact_monitor_result = Mock(
        wraps=PullRequestOverseer(repo="arthexis/arthexis").compact_monitor_result
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
            "--compact",
            stdout=buffer,
        )

    payload = json.loads(buffer.getvalue())
    assert payload["outcome"] == "waiting"
    assert payload["pendingChecks"] == ["Tests"]
    fake.compact_monitor_result.assert_called_once_with(monitor_payload)


def test_management_command_monitor_defaults_validation_to_patchwork(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Terminal")
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


def test_management_command_monitor_run_test_plan_blocks_non_terminal(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_ROLE", "Satellite")
    fake = PullRequestOverseer(repo="arthexis/arthexis")
    fake.monitor = Mock(return_value={"status": "complete"})

    with patch(
        "apps.repos.management.commands.pr_oversee.PullRequestOverseer",
        return_value=fake,
    ):
        with pytest.raises(CommandError, match="Local PR development is disabled"):
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
            )

    fake.monitor.assert_not_called()
