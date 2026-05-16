from pathlib import Path
from types import SimpleNamespace

from apps.core.tasks.auto_upgrade import tasks


def _mode(**overrides):
    defaults = {
        "mode": "stable",
        "admin_override": False,
        "override_log": None,
        "mode_file_exists": True,
        "mode_file_physical": True,
        "interval_minutes": 60,
        "requires_pypi": False,
        "branch": "main",
        "include_live_branch": False,
        "allowed_version_bumps": None,
    }
    defaults.update(overrides)
    return tasks.AutoUpgradeMode(**defaults)


def _repo_state(**overrides):
    defaults = {
        "remote_revision": "remote-rev",
        "release_version": "1.0.1",
        "release_revision": "release-rev",
        "release_pypi_url": "https://pypi.org/project/arthexis/1.0.1/",
        "remote_version": "1.0.1",
        "local_version": "1.0.0",
        "local_revision": "local-rev",
        "severity": tasks.SEVERITY_NORMAL,
    }
    defaults.update(overrides)
    return tasks.AutoUpgradeRepositoryState(**defaults)


def test_build_upgrade_decision_applies_stable_and_unstable(monkeypatch):
    class _ReleaseModel:
        @staticmethod
        def matches_revision(_version, _revision):
            return True

    monkeypatch.setattr(tasks, "_get_package_release_model", lambda: _ReleaseModel)
    expected_script = (
        "upgrade.bat"
        if tasks.os.name == "nt"
        else "upgrade.sh"
    )

    stable_decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(),
    )
    unstable_decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="unstable"),
        _repo_state(release_version=None, release_revision=None, remote_version="1.0.2"),
    )

    assert stable_decision.apply is True
    assert len(stable_decision.args) == 2
    assert Path(stable_decision.args[0]).name == expected_script
    assert stable_decision.args[1] == "--stable"
    assert unstable_decision.apply is True
    assert len(unstable_decision.args) == 2
    assert Path(unstable_decision.args[0]).name == expected_script
    assert unstable_decision.args[1] == "--latest"


def test_build_upgrade_decision_blocks_stable_major_upgrade(monkeypatch):
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="2.0.0",
            local_version="1.9.9",
        ),
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "major-upgrade-disallowed"


def test_build_upgrade_decision_blocks_stable_minor_upgrade(monkeypatch):
    checked_intervals: list[int] = []

    def _ran_recently(_base_dir, interval_minutes):
        checked_intervals.append(interval_minutes)
        return interval_minutes == 43200

    monkeypatch.setattr(tasks, "_auto_upgrade_ran_recently", _ran_recently)

    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="1.1.0",
            local_version="1.0.9",
        ),
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "minor-upgrade-disallowed"
    assert checked_intervals == []


def test_build_upgrade_decision_regular_uses_regular_channel_for_minor(monkeypatch):
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="regular"),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="1.1.0",
            local_version="1.0.9",
        ),
    )

    assert decision.apply is True
    assert decision.args[1] == "--regular"


def test_build_upgrade_decision_throttles_regular_major_upgrade(monkeypatch):
    monkeypatch.setattr(tasks, "_auto_upgrade_ran_recently", lambda *_args: True)

    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="regular"),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="2.0.0",
            local_version="1.9.9",
        ),
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "major-upgrade-not-due"


def test_build_upgrade_decision_latest_ignores_release_severity(monkeypatch):
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="unstable"),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="1.0.1",
            local_version="1.0.1",
            local_revision="local-rev",
            remote_revision="remote-rev",
            severity=tasks.SEVERITY_LOW,
        ),
    )

    assert decision.apply is True
    assert decision.args[1] == "--latest"


def test_build_upgrade_decision_custom_blocks_disallowed_minor_upgrade():
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="custom", allowed_version_bumps=("patch",)),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="1.1.0",
            local_version="1.0.9",
        ),
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "minor-upgrade-disallowed"


def test_build_upgrade_decision_custom_allows_selected_branch_version_bump():
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(
            mode="custom",
            branch="lab/canary",
            allowed_version_bumps=("major",),
        ),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="2.0.0",
            local_version="1.9.9",
        ),
    )

    assert decision.apply is True
    assert decision.args[1:] == ["--regular", "--branch", "lab/canary"]


def test_build_upgrade_decision_custom_live_branch_applies_same_version_revision():
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(
            mode="custom",
            branch="lab/canary",
            include_live_branch=True,
            allowed_version_bumps=(),
        ),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="1.0.0",
            local_version="1.0.0",
            remote_revision="remote-rev",
            local_revision="local-rev",
        ),
    )

    assert decision.apply is True
    assert decision.args[1:] == ["--latest", "--branch", "lab/canary"]


def test_build_upgrade_decision_custom_skips_same_version_without_live_branch():
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(
            mode="custom",
            branch="lab/canary",
            include_live_branch=False,
            allowed_version_bumps=("patch", "minor", "major"),
        ),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="1.0.0",
            local_version="1.0.0",
            remote_revision="remote-rev",
            local_revision="local-rev",
        ),
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "version-unchanged"


def test_build_upgrade_decision_custom_defaults_to_patch_and_minor_bumps():
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="custom"),
        _repo_state(
            release_version=None,
            release_revision=None,
            remote_version="1.1.0",
            local_version="1.0.9",
        ),
    )

    assert decision.apply is True
    assert decision.args[1:] == ["--regular"]


def test_resolve_auto_upgrade_mode_reads_custom_policy_controls(tmp_path):
    policy = SimpleNamespace(
        channel="custom",
        interval_minutes=42,
        requires_pypi_packages=True,
        pk=12,
        name="Canary",
        target_branch="origin/lab/canary",
        include_live_branch=True,
        allow_patch_upgrades=True,
        allow_minor_upgrades=False,
        allow_major_upgrades=True,
    )

    mode = tasks._resolve_auto_upgrade_mode(tmp_path, None, policy=policy)

    assert mode.mode == "custom"
    assert mode.interval_minutes == 42
    assert mode.requires_pypi is True
    assert mode.branch == "lab/canary"
    assert mode.include_live_branch is True
    assert mode.allowed_version_bumps == ("patch", "major")


def test_resolve_auto_upgrade_mode_custom_override_uses_default_bumps(tmp_path):
    mode = tasks._resolve_auto_upgrade_mode(tmp_path, "custom")

    assert mode.mode == "custom"
    assert mode.allowed_version_bumps == ("patch", "minor")


def test_resolve_auto_upgrade_mode_ignores_branch_for_builtin_policy(tmp_path):
    policy = SimpleNamespace(
        channel="regular",
        interval_minutes=42,
        requires_pypi_packages=False,
        pk=12,
        name="Regular",
        target_branch="lab/canary",
    )

    mode = tasks._resolve_auto_upgrade_mode(tmp_path, None, policy=policy)

    assert mode.mode == "regular"
    assert mode.branch == "main"


def test_normalize_upgrade_branch_rejects_invalid_git_ref_names():
    invalid_branches = [
        "lab:canary",
        "lab~canary",
        "lab^canary",
        "lab?canary",
        "lab*canary",
        "lab[canary",
        "lab\\canary",
        "lab;canary",
        "lab&canary",
        "lab|canary",
        "lab(canary)",
        "lab>canary",
        "lab//canary",
        "lab/canary.",
        "lab/canary.lock",
        "lab/.hidden",
        ".hidden",
        "@",
    ]

    for branch in invalid_branches:
        assert tasks._normalize_upgrade_branch(branch) == "main"

    assert tasks._normalize_upgrade_branch("refs/heads/lab/canary") == "lab/canary"
    assert tasks._normalize_upgrade_branch("feature+canary") == "feature+canary"
    assert tasks._normalize_upgrade_branch("release=2026") == "release=2026"
    assert tasks._normalize_upgrade_branch("ops]hotfix") == "ops]hotfix"
    assert tasks._normalize_upgrade_branch("lab$canary") == "lab$canary"


def test_ci_status_for_revision_compatibility_shim_returns_empty_string(tmp_path):
    assert tasks._ci_status_for_revision(tmp_path, "abc123") == ""


def test_build_upgrade_decision_skips_when_pypi_gate_blocks(monkeypatch):
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(requires_pypi=True),
        _repo_state(release_pypi_url=None),
    )

    assert decision.skip is True
    assert decision.reason == "pypi-release-missing"


def test_build_upgrade_decision_skips_stable_when_release_revision_mismatches(monkeypatch):
    class _ReleaseModel:
        @staticmethod
        def matches_revision(_version, _revision):
            return False

    monkeypatch.setattr(tasks, "_get_package_release_model", lambda: _ReleaseModel)

    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(),
    )

    assert decision.skip is True
    assert decision.reason == "release-revision-mismatch"


def test_build_upgrade_decision_skips_when_recency_throttled(monkeypatch):
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="unstable"),
        _repo_state(release_version=None, release_revision=None, remote_version="1.0.2"),
        recency_throttled=True,
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "recency-throttled"


def test_execute_upgrade_decision_rechecks_recency_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks, "_auto_upgrade_ran_recently", lambda *_args, **_kwargs: True)

    log_messages: list[str] = []
    ensure_calls: list[tuple[bool, bool]] = []
    executed: list[bool] = []
    startup_called: list[bool] = []

    def _append_log(_base_dir, message):
        log_messages.append(message)

    def _ensure_runtime_services(_base_dir, restart_if_active, revert_on_failure, log_appender):
        ensure_calls.append((restart_if_active, revert_on_failure))
        return True

    def _execute_upgrade_plan(*_args, **_kwargs):
        executed.append(True)

    monkeypatch.setattr(tasks, "append_auto_upgrade_log", _append_log)
    monkeypatch.setattr(tasks, "_execute_upgrade_plan", _execute_upgrade_plan)

    decision = tasks.AutoUpgradeDecision(
        skip=False,
        apply=True,
        reason=None,
        args=["./upgrade.sh", "--stable"],
        notify=True,
    )
    ops = tasks.AutoUpgradeOperations(
        git_fetch=lambda *_args, **_kwargs: None,
        resolve_remote_revision=lambda *_args, **_kwargs: "rev",
        ensure_runtime_services=_ensure_runtime_services,
        delegate_upgrade=lambda *_args, **_kwargs: None,
        run_upgrade_command=lambda *_args, **_kwargs: (None, True),
    )
    state = tasks.AutoUpgradeState()
    result = tasks._execute_upgrade_decision(
        tmp_path,
        _mode(mode="stable", interval_minutes=60),
        _repo_state(),
        decision,
        tmp_path / "auto-upgrade.log",
        notify=None,
        startup=lambda: startup_called.append(True),
        ops=ops,
        state=state,
    )

    assert result is False
    assert executed == []
    assert ensure_calls == [(False, False)]
    assert startup_called == [True]
    assert any("last run was less than 60 minutes ago" in message for message in log_messages)


def test_execute_upgrade_decision_normalizes_batch_upgrade_command(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks.os, "name", "posix")
    monkeypatch.setattr(tasks, "_auto_upgrade_ran_recently", lambda *_args, **_kwargs: False)

    executed_args: list[list[str]] = []
    log_messages: list[str] = []

    def _append_log(_base_dir, message):
        log_messages.append(message)

    def _execute_upgrade_plan(
        _base_dir,
        _mode_value,
        _repo_state,
        args,
        _upgrade_was_applied,
        _log_file,
        _ops,
        _state,
    ):
        executed_args.append(args)

    monkeypatch.setattr(tasks, "append_auto_upgrade_log", _append_log)
    monkeypatch.setattr(tasks, "_execute_upgrade_plan", _execute_upgrade_plan)

    decision = tasks.AutoUpgradeDecision(
        skip=False,
        apply=True,
        reason=None,
        args=["upgrade.bat", "--stable"],
        notify=False,
    )
    ops = tasks.AutoUpgradeOperations(
        git_fetch=lambda *_args, **_kwargs: None,
        resolve_remote_revision=lambda *_args, **_kwargs: "rev",
        ensure_runtime_services=lambda *_args, **_kwargs: True,
        delegate_upgrade=lambda *_args, **_kwargs: None,
        run_upgrade_command=lambda *_args, **_kwargs: (None, True),
    )
    state = tasks.AutoUpgradeState()
    result = tasks._execute_upgrade_decision(
        tmp_path,
        _mode(mode="stable"),
        _repo_state(),
        decision,
        tmp_path / "auto-upgrade.log",
        notify=None,
        startup=None,
        ops=ops,
        state=state,
    )

    assert result is True
    assert executed_args == [["./upgrade.sh", "--stable"]]
    assert "Normalized upgrade command for POSIX host" in log_messages
