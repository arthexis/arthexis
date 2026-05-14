from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PACKAGE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "operator-framework-core"
    / "skills"
)


def load_script(relative_path: str) -> ModuleType:
    path = PACKAGE_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pr_oversee_blocks_unstable_and_accepts_status_context_state():
    script = load_script("arthexis-pr-oversee/scripts/pr_oversee.py")
    pr = {
        "number": 1,
        "mergeStateStatus": "UNSTABLE",
        "statusCheckRollup": [
            {"context": "legacy-ci", "state": "SUCCESS"},
            {"name": "queued-ci", "state": "PENDING"},
        ],
    }

    readiness = script.readiness(pr, require_approval=False, allow_pending=False)

    assert "merge_state:UNSTABLE" in readiness["blockers"]
    assert "pending:queued-ci:PENDING" in readiness["blockers"]
    assert not any("legacy-ci" in blocker for blocker in readiness["blockers"])


def test_priority_suggestions_treats_has_hooks_as_mergeable_state():
    script = load_script("arthexis-pr-oversee/scripts/pr_priority_suggestions.py")
    assert "HAS_HOOKS" not in script.BAD_MERGE_STATES
    assert "UNSTABLE" in script.BAD_MERGE_STATES


def test_priority_checkout_command_includes_repo():
    script = load_script("arthexis-pr-oversee/scripts/pr_priority_suggestions.py")

    command = script.command_for("example/project", {"number": 17}, "checkout")

    assert "--repo example/project checkout --pr 17" in command


def test_priority_thread_lookup_failure_suppresses_merge(monkeypatch):
    script = load_script("arthexis-pr-oversee/scripts/pr_priority_suggestions.py")
    monkeypatch.setattr(
        script,
        "review_thread_counts",
        lambda repo, number: {
            "unresolved": 0,
            "currentUnresolved": 0,
            "threadLookupFailed": 1,
        },
    )
    pr = {
        "number": 17,
        "title": "Ready PR",
        "author": {"login": "dev"},
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }

    summary = script.summarize_pr("example/project", pr, include_threads=True)

    assert summary["suggestedAction"] == "review"
    assert summary["threadLookupFailed"] is True
    assert summary["priorityReason"] == "review thread lookup failed"


def test_review_thread_list_handles_missing_pull_request(monkeypatch, capsys):
    script = load_script("arthexis-review-attend/scripts/review_thread.py")
    monkeypatch.setattr(
        script,
        "_run_gh_graphql",
        lambda query, variables: {"data": {"repository": {"pullRequest": None}}},
    )

    with pytest.raises(SystemExit) as exc:
        script.list_threads(SimpleNamespace(repo="example/project", pr=999, unresolved=False))

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"threads": [], "error": "pull request not found"}


def test_release_preflight_blocks_missing_evidence_without_release_advice():
    script = load_script("release-readiness-publish/scripts/release_preflight.py")
    result = {
        "git": {
            "status": {"stdout": ""},
            "head": {"stdout": "abc"},
            "originMain": {"stdout": "abc"},
            "remoteTag": {"stdout": ""},
        },
        "latestRelease": {"error": "GraphQL: Repository not found"},
        "openPullRequests": {"error": "gh not found", "returncode": 127},
        "readinessIssue": {"error": "authentication failed"},
        "releaseForVersion": {"error": "release not found"},
        "pypi": {"exists": None, "error": "network unavailable"},
        "version": "1.2.3",
        "nextPatchVersion": "1.2.4",
    }

    decision = script.decide(result)

    assert decision["blocked"] is True
    assert any("latestRelease lookup failed" in item for item in decision["blockers"])
    assert any("openPullRequests lookup failed" in item for item in decision["blockers"])
    assert any("readinessIssue lookup failed" in item for item in decision["blockers"])
    assert any("PyPI lookup failed" in item for item in decision["blockers"])
    assert not decision["actions"]


def test_release_preflight_blocks_failed_git_probes_without_release_advice():
    script = load_script("release-readiness-publish/scripts/release_preflight.py")
    result = {
        "git": {
            "status": {"returncode": 0, "stdout": ""},
            "head": {"returncode": 128, "stdout": "", "stderr": "not a git repository"},
            "originMain": {"returncode": 128, "stdout": "", "stderr": "unknown revision"},
            "remoteTag": {"returncode": 0, "stdout": ""},
        },
        "latestRelease": [],
        "openPullRequests": [],
        "readinessIssue": [],
        "releaseForVersion": {"error": "release not found"},
        "pypi": {"exists": False},
        "version": "1.2.3",
        "nextPatchVersion": "1.2.4",
    }

    decision = script.decide(result)

    assert decision["blocked"] is True
    assert any("git head probe failed" in item for item in decision["blockers"])
    assert any("git originMain probe failed" in item for item in decision["blockers"])
    assert not decision["actions"]


def test_release_preflight_blocks_failed_fetch_without_release_advice():
    script = load_script("release-readiness-publish/scripts/release_preflight.py")
    result = {
        "fetch": {"returncode": 128, "stderr": "could not read from remote repository"},
        "git": {
            "status": {"returncode": 0, "stdout": ""},
            "head": {"returncode": 0, "stdout": "abc"},
            "originMain": {"returncode": 0, "stdout": "abc"},
            "remoteTag": {"returncode": 0, "stdout": ""},
        },
        "latestRelease": [],
        "openPullRequests": [],
        "readinessIssue": [],
        "releaseForVersion": {"error": "release not found"},
        "pypi": {"exists": False},
        "version": "1.2.3",
        "nextPatchVersion": "1.2.4",
    }

    decision = script.decide(result)

    assert decision["blocked"] is True
    assert any("git fetch probe failed" in item for item in decision["blockers"])
    assert not decision["actions"]


def test_release_tag_fails_tag_absent_when_remote_probe_errors(monkeypatch):
    script = load_script("release-readiness-publish/scripts/release_tag.py")

    def fake_run(cmd: list[str], cwd: Path) -> dict[str, object]:
        if cmd == ["git", "status", "--short"]:
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if cmd[:4] == ["git", "rev-parse", "-q", "--verify"]:
            return {"returncode": 1, "stdout": "", "stderr": ""}
        if cmd[:3] == ["git", "ls-remote", "--tags"]:
            return {"returncode": 128, "stdout": "", "stderr": "auth failed"}
        raise AssertionError(cmd)

    monkeypatch.setattr(script, "run", fake_run)
    args = SimpleNamespace(write=False, push=False, allow_dirty=False, remote="origin")

    output = script.collect_checks(Path.cwd(), "v1.2.3", args)

    tag_absent = next(check for check in output["checks"] if check["name"] == "tag-absent")
    assert tag_absent["ok"] is False
    assert tag_absent["detail"] == "remote tag probe failed"
