#!/usr/bin/env python3
"""Resolve GitHub targets and manage PR review threads through gh GraphQL."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _run_gh_graphql(query: str, variables: dict[str, str | int]) -> dict:
    if not shutil.which("gh"):
        raise SystemExit("gh CLI not found on PATH")
    command = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        flag = "-F" if isinstance(value, int) else "-f"
        command.extend([flag, f"{key}={value}"])
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    return json.loads(completed.stdout)


def _split_repo(value: str) -> tuple[str, str]:
    try:
        owner, repo = value.split("/", 1)
    except ValueError as exc:
        raise SystemExit("--repo must use owner/name format") from exc
    if not owner or not repo:
        raise SystemExit("--repo must use owner/name format")
    return owner, repo


def list_threads(args: argparse.Namespace) -> None:
    owner, repo = _split_repo(args.repo)
    query = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(last:5) {
            nodes {
              id
              author { login }
              body
              url
            }
          }
        }
      }
    }
  }
}
"""
    payload = _run_gh_graphql(
        query,
        {"owner": owner, "repo": repo, "number": args.pr},
    )
    threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    if args.unresolved:
        threads = [thread for thread in threads if not thread["isResolved"]]
    print(json.dumps({"threads": threads}, indent=2))


def resolve_target(args: argparse.Namespace) -> None:
    owner, repo = _split_repo(args.repo)
    query = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    issueOrPullRequest(number:$number) {
      __typename
      ... on PullRequest {
        number
        title
        state
        url
        headRefName
        headRefOid
        isDraft
        mergeStateStatus
        mergeable
        reviewDecision
      }
      ... on Issue {
        number
        title
        state
        url
        labels(first:20) {
          nodes { name }
        }
        assignees(first:10) {
          nodes { login }
        }
      }
    }
  }
}
"""
    payload = _run_gh_graphql(
        query,
        {"owner": owner, "repo": repo, "number": args.number},
    )
    target = payload["data"]["repository"]["issueOrPullRequest"]
    if target is None:
        raise SystemExit(f"#{args.number} was not found in {args.repo}")
    target_type = target.pop("__typename")
    print(
        json.dumps(
            {"number": args.number, "targetType": target_type, "target": target},
            indent=2,
        )
    )


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8").strip()
    if args.body:
        return args.body.strip()
    raise SystemExit("reply-resolve requires --body or --body-file")


def _reply_summary_body(args: argparse.Namespace) -> str:
    changes = [item.strip() for item in args.change if item.strip()]
    validations = [item.strip() for item in args.validation if item.strip()]
    notes = [item.strip() for item in args.note if item.strip()]
    commit = args.commit.strip()[:12]
    lines = [f"Addressed in {commit}." if commit else "Addressed."]
    if changes:
        lines.extend(["", "Changes:"])
        lines.extend(f"- {item}" for item in changes)
    if validations:
        lines.extend(["", "Validation:"])
        lines.extend(f"- {item}" for item in validations)
    if notes:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {item}" for item in notes)
    return "\n".join(lines).strip()


def reply_summary(args: argparse.Namespace) -> None:
    print(_reply_summary_body(args))


def reply_resolve(args: argparse.Namespace) -> None:
    body = _read_body(args)
    reply_query = """
mutation($threadId:ID!, $body:String!) {
  addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId, body:$body}) {
    comment { id url }
  }
}
"""
    result = _run_gh_graphql(
        reply_query,
        {"threadId": args.thread_id, "body": body},
    )
    output = {"reply": result["data"]["addPullRequestReviewThreadReply"]["comment"]}
    if args.resolve:
        resolve_query = """
mutation($threadId:ID!) {
  resolveReviewThread(input:{threadId:$threadId}) {
    thread { id isResolved }
  }
}
"""
        resolved = _run_gh_graphql(resolve_query, {"threadId": args.thread_id})
        output["resolved"] = resolved["data"]["resolveReviewThread"]["thread"]
    print(json.dumps(output, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser(
        "resolve-target",
        help="Resolve a numeric GitHub target to an Issue or PullRequest.",
    )
    resolve_parser.add_argument(
        "--repo", required=True, help="GitHub repo as owner/name."
    )
    resolve_parser.add_argument(
        "--number", required=True, type=int, help="Issue or PR number."
    )
    resolve_parser.set_defaults(func=resolve_target)

    list_parser = subparsers.add_parser("list", help="List PR review threads.")
    list_parser.add_argument("--repo", required=True, help="GitHub repo as owner/name.")
    list_parser.add_argument(
        "--pr", required=True, type=int, help="Pull request number."
    )
    list_parser.add_argument(
        "--unresolved",
        action="store_true",
        help="Only include unresolved threads.",
    )
    list_parser.set_defaults(func=list_threads)

    summary_parser = subparsers.add_parser(
        "summary",
        help="Format a concise review-thread reply from structured bullets.",
    )
    summary_parser.add_argument("--commit", default="", help="Commit SHA.")
    summary_parser.add_argument(
        "--change", action="append", default=[], help="Change summary bullet."
    )
    summary_parser.add_argument(
        "--validation",
        action="append",
        default=[],
        help="Validation summary bullet.",
    )
    summary_parser.add_argument(
        "--note", action="append", default=[], help="Optional note bullet."
    )
    summary_parser.set_defaults(func=reply_summary)

    reply_parser = subparsers.add_parser(
        "reply-resolve",
        help="Reply to a review thread and resolve it by default.",
    )
    reply_parser.add_argument(
        "--thread-id", required=True, help="Review thread node ID."
    )
    reply_parser.add_argument("--body", help="Reply body text.")
    reply_parser.add_argument("--body-file", help="Path to a UTF-8 reply body file.")
    reply_parser.add_argument(
        "--no-resolve",
        action="store_false",
        dest="resolve",
        help="Reply without resolving the thread.",
    )
    reply_parser.set_defaults(func=reply_resolve, resolve=True)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
