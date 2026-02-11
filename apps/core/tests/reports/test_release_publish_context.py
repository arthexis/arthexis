import json
from pathlib import Path

from apps.core.views.reports.release_publish.state.context import load_release_context


def test_load_release_context_merges_sensitive_keys_from_lockfile(tmp_path: Path):
    lock_path = tmp_path / "release.lock"
    lock_path.write_text(
        json.dumps({"github_token": "secret", "step": 2}),
        encoding="utf-8",
    )

    session_ctx = {"step": 1, "started": True}
    loaded = load_release_context(session_ctx, lock_path)

    assert loaded["step"] == 1
    assert loaded["started"] is True
    assert loaded["github_token"] == "secret"
