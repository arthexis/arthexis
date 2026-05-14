from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

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
