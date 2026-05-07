"""Deterministic GitHub pull-request oversight command."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.release import DEFAULT_PACKAGE
from apps.repos.github import parse_repository_url, resolve_active_repository
from apps.repos.pr_oversee import (
    PullRequestOverseeError,
    PullRequestOverseer,
    default_patchwork_dir,
    patchwork_worktree_path,
)


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

        test_plan_parser = subparsers.add_parser(
            "test-plan", help="Map changed files to test commands."
        )
        self._add_pr_arg(test_plan_parser)

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
            "--write",
            action="store_true",
            help="Required for monitor local validation, merge, and cleanup actions.",
        )

    def handle(self, *args, **options) -> None:
        repo = self._resolve_repository(str(options.get("repo") or ""))
        overseer = PullRequestOverseer(repo=repo)
        action = str(options["action"])

        try:
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
        if action == "checkout":
            worktree = self._resolve_worktree_option(overseer, number, options)
            return overseer.checkout(
                number,
                worktree=worktree,
                branch=str(options.get("branch") or ""),
            )
        if action == "test-plan":
            return overseer.test_plan(number)
        if action == "ci":
            return overseer.ci_failures(number, include_logs=bool(options.get("logs")))
        if action == "dependency-dedupe":
            return overseer.dependency_dedupe(limit=int(options.get("limit") or 80))
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
            return overseer.monitor(
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
        raise CommandError(f"Unsupported action: {action}")

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

    def _resolve_repository(self, raw_repo: str) -> str:
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
        return f"{active.owner}/{active.name}"

    def _write_result(self, result: dict[str, object], *, json_output: bool) -> None:
        if json_output:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
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

        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
