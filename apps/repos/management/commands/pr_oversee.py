"""Deterministic GitHub pull-request oversight command."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.nodes.models import Node
from apps.release import DEFAULT_PACKAGE
from apps.repos.github import parse_repository_url, resolve_active_repository
from apps.repos.github_monitor import local_node_role
from apps.repos.models import RepositoryWorkAssignment
from apps.repos.pr_oversee import (
    PullRequestOverseeError,
    PullRequestOverseer,
    default_patchwork_dir,
    patchwork_worktree_path,
    render_test_plan_markdown,
    review_reply_summary,
)
from apps.repos.services import work_assignments
from utils.env import env_bool


def _result_list(result: dict[str, object], key: str) -> list[object]:
    value = result.get(key)
    return value if isinstance(value, list) else []


def _pr_markdown_header(
    result: dict[str, object], heading: str, extra_lines: list[str]
) -> list[str]:
    return [
        heading,
        "",
        f"- Repo: `{result.get('repo')}`",
        f"- Title: {result.get('title') or ''}",
        (
            f"- State: `{result.get('state')}` "
            f"draft={str(bool(result.get('isDraft'))).lower()}"
        ),
        f"- Head: `{result.get('headRefName')}` `{str(result.get('headRefOid') or '')[:12]}`",
        *extra_lines,
        "",
    ]


def _review_severity_lines(result: dict[str, object]) -> list[str]:
    counts = result.get("severityCounts")
    if not isinstance(counts, dict) or not counts:
        return []
    return [
        "- Severity counts: " + ", ".join(f"{key}: {counts[key]}" for key in counts),
        "",
    ]


def _review_thread_markdown_line(raw_thread: object) -> str | None:
    if not isinstance(raw_thread, dict):
        return None
    location = raw_thread.get("path") or "(no path)"
    if raw_thread.get("line"):
        location = f"{location}:{raw_thread['line']}"
    state = "resolved" if raw_thread.get("isResolved") else "unresolved"
    if raw_thread.get("isOutdated"):
        state = f"{state}, outdated"
    return (
        f"- `{raw_thread.get('severity')}` `{location}` "
        f"[{state}] @{raw_thread.get('author') or 'unknown'}: "
        f"{raw_thread.get('summary') or ''}"
    )


def _domain_match_lines(raw_match: object) -> list[str]:
    if not isinstance(raw_match, dict):
        return []
    lines = [f"- {raw_match.get('name')}"]
    files = raw_match.get("files") if isinstance(raw_match.get("files"), list) else []
    lines.extend(f"  - `{path}`" for path in files[:8])
    if len(files) > 8:
        lines.append(f"  - ... {len(files) - 8} more files")
    return lines


def _domain_checklist_lines(matches: list[object]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_match in matches:
        if not isinstance(raw_match, dict):
            continue
        checklist = raw_match.get("checklist")
        if not isinstance(checklist, list):
            continue
        for item in checklist:
            text = str(item)
            if text in seen:
                continue
            seen.add(text)
            lines.append(f"- {text}")
    return lines


def _validation_command_lines(result: dict[str, object]) -> list[str]:
    commands = result.get("validationCommands")
    if not isinstance(commands, list):
        return []
    return [f"- `{command}`" for command in commands]


class Command(BaseCommand):
    """Expose deterministic PR oversight operations."""

    help = "Inspect, gate, prepare, validate, merge, and clean up GitHub pull requests."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--repo",
            default="",
            help=(
                "Repository slug in owner/name format. Defaults to active package repository "
                f"or {DEFAULT_PACKAGE.repository_url}."
            ),
        )
        parser.add_argument(
            "--json", action="store_true", help="Emit machine-readable JSON output."
        )

        subparsers = parser.add_subparsers(dest="action", required=True)

        inspect_parser = subparsers.add_parser(
            "inspect", help="Return a complete PR state snapshot."
        )
        self._add_pr_arg(inspect_parser)

        gate_parser = subparsers.add_parser(
            "gate", help="Fail unless the PR is merge-ready."
        )
        self._add_pr_arg(gate_parser)
        gate_parser.add_argument("--require-approval", action="store_true")
        gate_parser.add_argument("--allow-pending", action="store_true")

        comments_parser = subparsers.add_parser(
            "comments", help="List PR review threads."
        )
        self._add_pr_arg(comments_parser)
        comments_parser.add_argument("--unresolved", action="store_true")

        review_batch_parser = subparsers.add_parser(
            "review-batch",
            help="Summarize review threads into a stable patch batch order.",
        )
        self._add_pr_arg(review_batch_parser)
        review_batch_parser.add_argument("--include-resolved", action="store_true")
        review_batch_parser.add_argument(
            "--format",
            choices=["json", "markdown"],
            default="json",
            help="Output format when --json is not used.",
        )

        domain_preflight_parser = subparsers.add_parser(
            "domain-preflight",
            help="Build a GWAY/RFID/image-burn/node-registration preflight checklist.",
        )
        self._add_pr_arg(domain_preflight_parser)
        domain_preflight_parser.add_argument(
            "--format",
            choices=["json", "markdown"],
            default="json",
            help="Output format when --json is not used.",
        )

        checkout_parser = subparsers.add_parser(
            "checkout", help="Create an isolated worktree for a PR."
        )
        self._add_pr_arg(checkout_parser)
        checkout_parser.add_argument(
            "--worktree",
            default="",
            help="Worktree path to create. Defaults to the patchwork directory.",
        )
        self._add_patchwork_dir_arg(checkout_parser)
        checkout_parser.add_argument(
            "--branch", default="", help="Optional local branch name."
        )
        checkout_parser.add_argument(
            "--no-link-venv",
            action="store_true",
            help="Do not link the current checkout .venv into the PR worktree.",
        )

        test_plan_parser = subparsers.add_parser(
            "test-plan", help="Map changed files to test commands."
        )
        test_plan_parser.add_argument(
            "--pr",
            type=int,
            default=0,
            help="Pull request number. Omit when using --local or --changed-file.",
        )
        test_plan_parser.add_argument(
            "--changed-file",
            action="append",
            default=[],
            help="Changed file path to include in the plan. May be repeated.",
        )
        test_plan_parser.add_argument(
            "--local",
            action="store_true",
            help="Plan the local diff against --base-ref instead of a PR.",
        )
        test_plan_parser.add_argument(
            "--base-ref",
            default="origin/main",
            help="Base ref for --local diff planning.",
        )
        test_plan_parser.add_argument(
            "--format",
            choices=["json", "markdown"],
            default="json",
            help="Output format when --json is not used.",
        )

        ci_parser = subparsers.add_parser(
            "ci", help="Collect failed or pending CI checks."
        )
        self._add_pr_arg(ci_parser)
        ci_parser.add_argument(
            "--failures", action="store_true", help="Return failing/pending checks."
        )
        ci_parser.add_argument(
            "--logs", action="store_true", help="Fetch failed run log snippets."
        )

        dedupe_parser = subparsers.add_parser(
            "dependency-dedupe",
            help="Find duplicate or superseded dependency PR groups.",
        )
        dedupe_parser.add_argument("--limit", type=int, default=80)

        dependency_graph_parser = subparsers.add_parser(
            "dependency-graph",
            help="Report explicit cross-repository PR prerequisites.",
        )
        dependency_graph_parser.add_argument(
            "--dependency",
            action="append",
            default=[],
            help=(
                "Dependent and prerequisite PR references, for example "
                "arthexis/arthexis#9106=arthexis/arthexis#9105. May be repeated."
            ),
        )
        dependency_graph_parser.add_argument("--require-approval", action="store_true")
        dependency_graph_parser.add_argument("--allow-pending", action="store_true")

        closed_report_parser = subparsers.add_parser(
            "closed-report",
            help="Report recently closed pull requests.",
        )
        closed_report_parser.add_argument(
            "--since-hours",
            type=float,
            default=8.0,
            help="Look back this many hours from the current time.",
        )
        closed_report_parser.add_argument("--limit", type=int, default=100)

        report_parser = subparsers.add_parser(
            "report",
            help="Report PR activity, for example: report closed --since 8h.",
        )
        report_parser.add_argument("report_name", choices=["closed"])
        report_parser.add_argument(
            "--since",
            default="8h",
            help="Lookback duration such as 8h, 1d, or 30m.",
        )
        report_parser.add_argument("--limit", type=int, default=100)

        node_queue_parser = subparsers.add_parser(
            "node-queue",
            help="Rank open PRs for this node's role, app profile, and hardware tags.",
        )
        node_queue_parser.add_argument("--limit", type=int, default=80)
        node_queue_parser.add_argument("--include-drafts", action="store_true")
        node_queue_parser.add_argument(
            "--role",
            default="",
            help="Node role to rank against. Defaults to the local node role.",
        )
        node_queue_parser.add_argument(
            "--installed-app",
            action="append",
            default=[],
            help=(
                "Installed app selector to rank against. May be repeated and "
                "defaults to settings.INSTALLED_APPS."
            ),
        )
        node_queue_parser.add_argument(
            "--hardware-tag",
            action="append",
            default=[],
            help="Local hardware tag to rank against, for example rfid or raspberry-pi.",
        )
        self._add_patchwork_dir_arg(node_queue_parser)

        advance_parser = subparsers.add_parser(
            "advance",
            help="Prioritize and optionally advance open pull requests.",
        )
        advance_parser.add_argument("--limit", type=int, default=80)
        advance_parser.add_argument("--include-drafts", action="store_true")
        advance_parser.add_argument("--require-approval", action="store_true")
        advance_parser.add_argument("--allow-pending", action="store_true")
        advance_parser.add_argument(
            "--ready-drafts",
            action="store_true",
            help="Plan or mark otherwise-ready drafts as ready for review.",
        )
        advance_parser.add_argument(
            "--merge", action="store_true", help="Plan or merge ready PRs."
        )
        advance_parser.add_argument(
            "--method", choices=["squash", "merge", "rebase"], default="squash"
        )
        advance_parser.add_argument("--delete-branch", action="store_true")
        advance_parser.add_argument("--admin", action="store_true")
        advance_parser.add_argument(
            "--write",
            action="store_true",
            help="Required to mark drafts ready or merge PRs.",
        )

        merge_parser = subparsers.add_parser("merge", help="Gate and merge a PR.")
        self._add_pr_arg(merge_parser)
        merge_parser.add_argument(
            "--method", choices=["squash", "merge", "rebase"], default="squash"
        )
        merge_parser.add_argument("--delete-branch", action="store_true")
        merge_parser.add_argument("--require-approval", action="store_true")
        merge_parser.add_argument("--expected-head-sha", default="")
        merge_parser.add_argument("--allow-pending", action="store_true")
        merge_parser.add_argument("--admin", action="store_true")
        merge_parser.add_argument(
            "--write",
            action="store_true",
            help="Required to perform the merge. Without it the command only reports the gated plan.",
        )

        cleanup_parser = subparsers.add_parser(
            "cleanup", help="Clean local PR artifacts after merge."
        )
        self._add_pr_arg(cleanup_parser)
        cleanup_parser.add_argument(
            "--worktree", default="", help="Worktree path to remove."
        )
        cleanup_parser.add_argument("--delete-local-branch", default="")
        cleanup_parser.add_argument(
            "--write",
            action="store_true",
            help="Required to perform cleanup. Without it the command only reports the plan.",
        )

        hygiene_parser = subparsers.add_parser(
            "hygiene", help="Run deterministic PR hygiene checks."
        )
        self._add_pr_arg(hygiene_parser)

        patchwork_parser = subparsers.add_parser(
            "patchwork", help="Report or prune monitor-owned patchwork worktrees."
        )
        self._add_patchwork_dir_arg(patchwork_parser)
        patchwork_parser.add_argument("--max-age-days", type=float, default=14.0)
        patchwork_parser.add_argument("--force-stale-open", action="store_true")
        patchwork_parser.add_argument(
            "--write",
            action="store_true",
            help="Required to remove patchwork worktrees.",
        )

        reply_summary_parser = subparsers.add_parser(
            "reply-summary", help="Build a terse PR review reply body."
        )
        reply_summary_parser.add_argument("--commit", default="")
        reply_summary_parser.add_argument(
            "--change", action="append", default=[], help="Change summary bullet."
        )
        reply_summary_parser.add_argument(
            "--validation",
            action="append",
            default=[],
            help="Validation summary bullet.",
        )
        reply_summary_parser.add_argument(
            "--note", action="append", default=[], help="Optional note bullet."
        )
        reply_summary_parser.add_argument(
            "--feedback-issue",
            action="store_true",
            help="Render a short two-line response intended for feedback-ingested issues.",
        )

        monitor_parser = subparsers.add_parser(
            "monitor",
            help="Run the PR oversight workflow until completion or manual decision.",
        )
        self._add_pr_arg(monitor_parser)
        monitor_parser.add_argument("--interval", type=float, default=30.0)
        monitor_parser.add_argument("--max-iterations", type=int, default=120)
        monitor_parser.add_argument("--timeout", type=float, default=0.0)
        monitor_parser.add_argument("--require-approval", action="store_true")
        monitor_parser.add_argument("--allow-pending", action="store_true")
        monitor_parser.add_argument("--include-logs", action="store_true")
        monitor_parser.add_argument(
            "--run-test-plan",
            action="store_true",
            help="Run local validation commands from the selected checkout (requires --write).",
        )
        monitor_parser.add_argument("--dependency-limit", type=int, default=80)
        monitor_parser.add_argument(
            "--worktree", default="", help="Optional PR worktree path."
        )
        self._add_patchwork_dir_arg(monitor_parser)
        monitor_parser.add_argument(
            "--branch", default="", help="Optional local branch for checkout."
        )
        monitor_parser.add_argument(
            "--merge", action="store_true", help="Merge when the PR is ready."
        )
        monitor_parser.add_argument(
            "--cleanup", action="store_true", help="Clean local artifacts after merge."
        )
        monitor_parser.add_argument(
            "--method", choices=["squash", "merge", "rebase"], default="squash"
        )
        monitor_parser.add_argument("--delete-branch", action="store_true")
        monitor_parser.add_argument("--delete-local-branch", default="")
        monitor_parser.add_argument("--expected-head-sha", default="")
        monitor_parser.add_argument("--admin", action="store_true")
        monitor_parser.add_argument(
            "--compact",
            action="store_true",
            help="Return a concise monitor summary instead of the full payload.",
        )
        monitor_parser.add_argument(
            "--write",
            action="store_true",
            help="Required for monitor local validation, merge, and cleanup actions.",
        )

    def handle(self, *args, **options) -> None:
        action = str(options["action"])
        pr_number = int(options.get("pr") or 0)
        repo = self._resolve_repository(
            str(options.get("repo") or ""),
            allow_database_fallback=action == "test-plan" and not pr_number,
        )
        overseer = PullRequestOverseer(repo=repo)

        try:
            self._enforce_local_development_gate(action, options)
            result = self._run_action(overseer, action, options)
        except PullRequestOverseeError as exc:
            raise CommandError(str(exc)) from exc

        self._write_result(result, json_output=bool(options.get("json")))
        if action == "gate" and not result.get("ready"):
            raise CommandError(
                "PR is not merge-ready: " + ", ".join(result.get("blockers") or [])
            )
        if action == "hygiene" and not result.get("ok"):
            raise CommandError(
                "PR hygiene failed: " + ", ".join(result.get("failures") or [])
            )
        if action == "monitor" and result.get("manualDecisionRequired"):
            raise CommandError(
                "manual decision required: "
                + ", ".join(result.get("manualDecisionReasons") or [])
            )

    def _run_action(
        self,
        overseer: PullRequestOverseer,
        action: str,
        options: dict[str, object],
    ) -> dict[str, object]:
        number = int(options.get("pr") or 0)
        if action == "inspect":
            return overseer.inspect(number)
        if action == "gate":
            return overseer.gate(
                number,
                require_approval=bool(options.get("require_approval")),
                allow_pending=bool(options.get("allow_pending")),
            )
        if action == "comments":
            return overseer.comments(
                number, unresolved_only=bool(options.get("unresolved"))
            )
        if action == "review-batch":
            result = overseer.review_batch(
                number, include_resolved=bool(options.get("include_resolved"))
            )
            return self._with_optional_markdown(
                result,
                format_name=str(options.get("format") or "json"),
                renderer=self._render_review_batch_markdown,
            )
        if action == "domain-preflight":
            result = overseer.domain_preflight(number)
            return self._with_optional_markdown(
                result,
                format_name=str(options.get("format") or "json"),
                renderer=self._render_domain_preflight_markdown,
            )
        if action == "checkout":
            worktree = self._resolve_worktree_option(overseer, number, options)
            return overseer.checkout(
                number,
                worktree=worktree,
                branch=str(options.get("branch") or ""),
                link_venv=not bool(options.get("no_link_venv")),
            )
        if action == "test-plan":
            changed_files = [
                str(path)
                for path in options.get("changed_file") or []
                if str(path).strip()
            ]
            selector_count = (
                int(bool(changed_files))
                + int(bool(options.get("local")))
                + int(bool(number))
            )
            if selector_count > 1:
                raise PullRequestOverseeError(
                    "Choose only one selector for test-plan: --pr, --local, or --changed-file."
                )
            if changed_files:
                result = overseer.test_plan_for_files(
                    changed_files, source="changed-files"
                )
            elif options.get("local"):
                result = overseer.local_test_plan(
                    base_ref=str(options.get("base_ref") or "origin/main")
                )
            elif number:
                result = overseer.test_plan(number)
            else:
                result = overseer.test_plan_for_files([], source="changed-files")
            return self._with_optional_markdown(
                result,
                format_name=str(options.get("format") or "json"),
                renderer=render_test_plan_markdown,
            )
        if action == "ci":
            return overseer.ci_failures(number, include_logs=bool(options.get("logs")))
        if action == "dependency-dedupe":
            return overseer.dependency_dedupe(limit=int(options.get("limit") or 80))
        if action == "dependency-graph":
            return overseer.dependency_graph(
                dependencies=[str(value) for value in options.get("dependency") or []],
                require_approval=bool(options.get("require_approval")),
                allow_pending=bool(options.get("allow_pending")),
            )
        if action == "closed-report":
            return overseer.closed_pr_report(
                since_hours=float(options.get("since_hours") or 8.0),
                limit=int(options.get("limit") or 100),
            )
        if action == "report":
            if str(options.get("report_name") or "") == "closed":
                return overseer.closed_pr_report(
                    since_hours=self._parse_since_hours(
                        str(options.get("since") or "8h")
                    ),
                    limit=int(options.get("limit") or 100),
                )
        if action == "node-queue":
            node_context = self._node_queue_context(options)
            return overseer.node_queue(
                limit=int(options.get("limit") or 80),
                include_drafts=bool(options.get("include_drafts")),
                node_role=str(node_context["role"]),
                installed_apps=[
                    str(item) for item in node_context.get("installedApps") or []
                ],
                hardware_tags=[
                    str(item) for item in node_context.get("hardwareTags") or []
                ],
                local_development=(
                    node_context.get("localDevelopment")
                    if isinstance(node_context.get("localDevelopment"), dict)
                    else {}
                ),
            )
        if action == "advance":
            return overseer.advance(
                limit=int(options.get("limit") or 80),
                include_drafts=bool(options.get("include_drafts")),
                require_approval=bool(options.get("require_approval")),
                allow_pending=bool(options.get("allow_pending")),
                ready_drafts=bool(options.get("ready_drafts")),
                merge=bool(options.get("merge")),
                method=str(options.get("method") or "squash"),
                delete_branch=bool(options.get("delete_branch")),
                admin=bool(options.get("admin")),
                write=bool(options.get("write")),
            )
        if action == "merge":
            if not options.get("write"):
                gate = overseer.gate(
                    number,
                    require_approval=bool(options.get("require_approval")),
                    allow_pending=bool(options.get("allow_pending")),
                )
                return {"write": False, "plannedCommand": "gh pr merge", "gate": gate}
            return overseer.merge(
                number,
                method=str(options.get("method") or "squash"),
                delete_branch=bool(options.get("delete_branch")),
                require_approval=bool(options.get("require_approval")),
                expected_head_sha=str(options.get("expected_head_sha") or ""),
                allow_pending=bool(options.get("allow_pending")),
                admin=bool(options.get("admin")),
            )
        if action == "cleanup":
            if not options.get("write"):
                return {
                    "write": False,
                    "plannedActions": [
                        item
                        for item in (
                            "remove-worktree" if options.get("worktree") else "",
                            "fetch-base-prune",
                            (
                                "delete-local-branch"
                                if options.get("delete_local_branch")
                                else ""
                            ),
                        )
                        if item
                    ],
                }
            worktree = (
                Path(str(options["worktree"])).expanduser()
                if str(options.get("worktree") or "").strip()
                else None
            )
            return overseer.cleanup(
                number,
                worktree=worktree,
                delete_local_branch=str(options.get("delete_local_branch") or ""),
            )
        if action == "hygiene":
            return overseer.hygiene(number)
        if action == "patchwork":
            return overseer.patchwork_hygiene(
                root=self._resolve_patchwork_dir(options),
                max_age_days=float(options.get("max_age_days") or 0.0),
                write=bool(options.get("write")),
                force_stale_open=bool(options.get("force_stale_open")),
            )
        if action == "monitor":
            worktree = self._resolve_monitor_worktree(overseer, number, options)
            result = overseer.monitor(
                number,
                interval_seconds=float(options.get("interval") or 0.0),
                max_iterations=int(options.get("max_iterations") or 0),
                timeout_seconds=float(options.get("timeout") or 0.0),
                require_approval=bool(options.get("require_approval")),
                allow_pending=bool(options.get("allow_pending")),
                include_logs=bool(options.get("include_logs")),
                run_test_plan=bool(options.get("run_test_plan")),
                dependency_limit=int(options.get("dependency_limit") or 0),
                worktree=worktree,
                branch=str(options.get("branch") or ""),
                merge=bool(options.get("merge")),
                cleanup=bool(options.get("cleanup")),
                method=str(options.get("method") or "squash"),
                delete_branch=bool(options.get("delete_branch")),
                delete_local_branch=str(options.get("delete_local_branch") or ""),
                expected_head_sha=str(options.get("expected_head_sha") or ""),
                admin=bool(options.get("admin")),
                write=bool(options.get("write")),
            )
            if options.get("compact"):
                return overseer.compact_monitor_result(result)
            return result
        if action == "reply-summary":
            return review_reply_summary(
                commit=str(options.get("commit") or ""),
                changes=[str(item) for item in options.get("change") or []],
                validations=[str(item) for item in options.get("validation") or []],
                notes=[str(item) for item in options.get("note") or []],
                feedback_issue=bool(options.get("feedback_issue")),
            )
        raise CommandError(f"Unsupported action: {action}")

    def _enforce_local_development_gate(
        self, action: str, options: dict[str, object]
    ) -> None:
        if not self._requires_local_development(action, options):
            return

        role = self._resolve_node_role()
        patchwork = self._resolve_patchwork_dir(options)
        patchwork_exists = patchwork.exists()
        if role.strip().lower() == "terminal" and patchwork_exists:
            return
        policy = self._local_development_policy(options)
        if policy["allowed"]:
            return

        raise CommandError(
            "Local PR development is disabled on this node: "
            f"node_role={role or 'unknown'} patchwork_dir={patchwork} "
            f"patchwork_dir_exists={patchwork_exists} "
            f"authorized={policy['authorized']} role_allowed={policy['roleAllowed']}. "
            "Local PR development requires an authorized node dev environment and "
            "an existing assigned patchwork directory. Terminal is authorized by "
            "default; Satellite requires ARTHEXIS_NODE_DEV_ENV=1; Control requires "
            "an active operator patchwork assignment for the target PR; "
            "Watchtower and production roles stay read-only by default. Run only "
            "no-local-development oversight here, such as inspect, gate, comments, "
            "ci, hygiene, test-plan planning, dependency-dedupe, dependency-graph, node-queue, "
            "read-only advance, and read-only patchwork. Raise or update a GitHub "
            "issue with upstream-worktree context for required code changes."
        )

    def _requires_local_development(
        self, action: str, options: dict[str, object]
    ) -> bool:
        if action == "checkout":
            return True
        if action == "cleanup":
            return bool(options.get("write"))
        if action == "patchwork":
            return bool(options.get("write"))
        if action == "monitor":
            return bool(
                options.get("run_test_plan")
                or str(options.get("worktree") or "").strip()
                or (options.get("cleanup") and options.get("write"))
                or str(options.get("delete_local_branch") or "").strip()
            )
        return False

    def _resolve_node_role(self) -> str:
        return local_node_role()

    def _node_queue_context(self, options: dict[str, object]) -> dict[str, object]:
        role = str(options.get("role") or "").strip() or self._resolve_node_role()
        installed_apps = [
            str(item)
            for item in (
                options.get("installed_app") or getattr(settings, "INSTALLED_APPS", [])
            )
        ]
        hardware_tags = [str(item) for item in options.get("hardware_tag") or []]
        local_development = self._local_development_policy({**options, "role": role})
        return {
            "role": role,
            "installedApps": installed_apps,
            "hardwareTags": hardware_tags,
            "localDevelopment": local_development,
        }

    def _local_development_policy(
        self, options: dict[str, object]
    ) -> dict[str, object]:
        role = str(options.get("role") or "").strip() or self._resolve_node_role()
        normalized_role = role.casefold()
        patchwork = self._resolve_patchwork_dir(options)
        patchwork_exists = patchwork.exists()
        terminal_default = normalized_role == "terminal"
        explicit_dev_env = env_bool("ARTHEXIS_NODE_DEV_ENV", False)
        protected_role = normalized_role in {"watchtower", "constellation"}
        control_assignment = False
        role_allowed = not protected_role
        if normalized_role == "control":
            control_assignment = self._control_patchwork_assignment_authorized(options)
            authorized = control_assignment
        else:
            authorized = terminal_default or explicit_dev_env
        allowed = patchwork_exists and authorized and role_allowed
        reasons = []
        if not patchwork_exists:
            reasons.append("missing-patchwork-dir")
        if not authorized:
            reasons.append(
                "missing-control-patchwork-assignment"
                if normalized_role == "control"
                else "missing-dev-env-authorization"
            )
        if protected_role:
            reasons.append("protected-role")
        return {
            "allowed": allowed,
            "role": role,
            "patchworkDir": str(patchwork),
            "patchworkDirExists": patchwork_exists,
            "authorized": authorized,
            "authorization": (
                (
                    "terminal-default"
                    if terminal_default
                    else (
                        "operator-control-patchwork-assignment"
                        if normalized_role == "control" and control_assignment
                        else "ARTHEXIS_NODE_DEV_ENV"
                    )
                )
                if authorized
                else ""
            ),
            "roleAllowed": role_allowed,
            "reasons": reasons,
        }

    def _control_patchwork_assignment_authorized(
        self, options: dict[str, object]
    ) -> bool:
        try:
            pr_number = int(options.get("pr") or 0)
            if pr_number <= 0:
                return False
            local_node = Node.get_local()
            if local_node is None:
                return False
            repo_slug = self._resolve_repository(
                str(options.get("repo") or ""),
                allow_database_fallback=False,
            )
            owner, name = repo_slug.split("/", 1)
            return RepositoryWorkAssignment.objects.filter(
                repository__owner=owner,
                repository__name=name,
                target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
                number=pr_number,
                node=local_node,
                patchwork_authorized=True,
                reason__icontains=work_assignments.CONTROL_MANUAL_PATCHWORK_REASON_MARKER,
                status=RepositoryWorkAssignment.Status.ACTIVE,
            ).exists()
        except (DatabaseError, CommandError, ValueError):
            return False

    def _add_pr_arg(self, parser) -> None:
        parser.add_argument(
            "--pr", type=int, required=True, help="Pull request number."
        )

    def _add_patchwork_dir_arg(self, parser) -> None:
        parser.add_argument(
            "--patchwork-dir",
            default="",
            help=(
                "Directory for temporary PR worktrees. Defaults to "
                f"{default_patchwork_dir()} or ARTHEXIS_PATCHWORK_DIR."
            ),
        )

    def _resolve_patchwork_dir(self, options: dict[str, object]) -> Path:
        raw_value = str(options.get("patchwork_dir") or "").strip()
        if raw_value:
            return Path(raw_value).expanduser()
        return default_patchwork_dir()

    def _resolve_worktree_option(
        self,
        overseer: PullRequestOverseer,
        number: int,
        options: dict[str, object],
    ) -> Path:
        raw_value = str(options.get("worktree") or "").strip()
        if raw_value:
            return Path(raw_value).expanduser()
        return patchwork_worktree_path(
            self._resolve_patchwork_dir(options), overseer.repo, number
        )

    def _resolve_monitor_worktree(
        self,
        overseer: PullRequestOverseer,
        number: int,
        options: dict[str, object],
    ) -> Path | None:
        raw_value = str(options.get("worktree") or "").strip()
        if raw_value:
            return Path(raw_value).expanduser()
        if options.get("run_test_plan"):
            return patchwork_worktree_path(
                self._resolve_patchwork_dir(options), overseer.repo, number
            )
        return None

    def _resolve_repository(
        self, raw_repo: str, *, allow_database_fallback: bool = False
    ) -> str:
        cleaned = raw_repo.strip()
        if cleaned:
            try:
                owner, name = parse_repository_url(cleaned)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            return f"{owner}/{name}"

        try:
            active = resolve_active_repository()
        except ValueError:
            owner, name = parse_repository_url(DEFAULT_PACKAGE.repository_url)
            return f"{owner}/{name}"
        except DatabaseError:
            if not allow_database_fallback:
                raise
            owner, name = parse_repository_url(DEFAULT_PACKAGE.repository_url)
            return f"{owner}/{name}"
        return f"{active.owner}/{active.name}"

    def _parse_since_hours(self, raw_value: str) -> float:
        cleaned = raw_value.strip().lower()
        if not cleaned:
            raise CommandError("--since must look like 30m, 8h, or 1d")
        if cleaned[-1].isalpha():
            unit = cleaned[-1]
            amount = cleaned[:-1].strip()
        else:
            unit = "h"
            amount = cleaned
        amount_parts = amount.split(".")
        if (
            unit not in {"m", "h", "d"}
            or len(amount_parts) > 2
            or not amount_parts[0].isdigit()
            or (len(amount_parts) == 2 and not amount_parts[1].isdigit())
        ):
            raise CommandError("--since must look like 30m, 8h, or 1d")
        value = float(amount)
        if unit == "m":
            return value / 60
        if unit == "d":
            return value * 24
        return value

    def _with_optional_markdown(
        self,
        result: dict[str, object],
        *,
        format_name: str,
        renderer,
    ) -> dict[str, object]:
        if format_name == "markdown":
            return {**result, "markdown": renderer(result)}
        return result

    def _render_review_batch_markdown(self, result: dict[str, object]) -> str:
        lines = _pr_markdown_header(
            result,
            f"# PR #{result.get('number')} Review Batch",
            [f"- Unresolved: `{result.get('unresolvedCount')}`"],
        )
        lines.extend(_review_severity_lines(result))
        threads = _result_list(result, "threads")
        if not threads:
            lines.append("No matching review threads.")
            return "\n".join(lines)
        lines.extend(["## Threads", ""])
        for raw_thread in threads:
            line = _review_thread_markdown_line(raw_thread)
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _render_domain_preflight_markdown(self, result: dict[str, object]) -> str:
        lines = _pr_markdown_header(
            result,
            f"# Domain Preflight for PR #{result.get('number')}",
            [f"- Risk: `{result.get('risk')}`"],
        )
        matches = _result_list(result, "matches")
        if not matches:
            lines.append("No GWAY/RFID reservation domains were detected.")
            return "\n".join(lines)
        lines.extend(["## Matched Domains", ""])
        for raw_match in matches:
            lines.extend(_domain_match_lines(raw_match))
        lines.extend(["", "## Required Checks", ""])
        lines.extend(_domain_checklist_lines(matches))
        lines.extend(["", "## Validation Commands", ""])
        lines.extend(_validation_command_lines(result))
        return "\n".join(lines)

    def _write_result(self, result: dict[str, object], *, json_output: bool) -> None:
        if json_output:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            return

        if "markdown" in result:
            self.stdout.write(str(result["markdown"]))
            return

        if "ready" in result:
            state = "READY" if result.get("ready") else "BLOCKED"
            self.stdout.write(f"state={state}")
            for blocker in result.get("blockers") or []:
                self.stdout.write(f"blocker={blocker}")
            for warning in result.get("warnings") or []:
                self.stdout.write(f"warning={warning}")
            return

        if "ok" in result:
            state = "OK" if result.get("ok") else "FAILED"
            self.stdout.write(f"hygiene={state}")
            for failure in result.get("failures") or []:
                self.stdout.write(f"failure={failure}")
            for warning in result.get("warnings") or []:
                self.stdout.write(f"warning={warning}")
            return

        if "manualDecisionRequired" in result:
            self.stdout.write(f"monitor={result.get('status')}")
            for reason in result.get("manualDecisionReasons") or []:
                self.stdout.write(f"manual={reason}")
            return

        if "body" in result:
            self.stdout.write(str(result.get("body") or "").rstrip())
            return

        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
