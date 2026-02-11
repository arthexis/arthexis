import subprocess
from collections.abc import Sequence

from apps.core.views.reports.release_publish.services.git_ops import (
    GitProcessAdapter,
    collect_dirty_files,
    has_upstream,
    push_needed,
    working_tree_dirty,
)


class FakeGitAdapter(GitProcessAdapter):
    def __init__(self, responses):
        self.responses = responses

    def run(self, args: Sequence[str], *, check: bool = True):
        key = tuple(args)
        proc = self.responses[key]
        if check and proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, list(args), output=proc.stdout, stderr=proc.stderr)
        return proc


def _proc(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def test_dirty_state_and_file_collection():
    adapter = FakeGitAdapter({
        ("git", "status", "--porcelain"): _proc(" M VERSION\n?? apps/core/fixtures/releases__x.json\n"),
    })
    assert working_tree_dirty(adapter) is True
    dirty = collect_dirty_files(adapter)
    assert dirty[0]["path"] == "VERSION"
    assert dirty[1]["path"].endswith("releases__x.json")


def test_has_upstream_true_false():
    adapter = FakeGitAdapter({
        ("git", "rev-parse", "--abbrev-ref", "main@{upstream}"): _proc(code=0),
        ("git", "rev-parse", "--abbrev-ref", "feat@{upstream}"): _proc(code=1),
    })
    assert has_upstream(adapter, "main") is True
    assert has_upstream(adapter, "feat") is False


def test_push_needed_compares_remote_head():
    adapter = FakeGitAdapter({
        ("git", "rev-parse", "main"): _proc("abc123\n"),
        ("git", "ls-remote", "--heads", "origin", "main"): _proc("def999\trefs/heads/main\n"),
    })
    assert push_needed(adapter, "origin", "main") is True
