import json
import logging
from pathlib import Path
from types import SimpleNamespace

from django.db import OperationalError

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


def test_build_upgrade_decision_applies_stable_and_unstable():
    expected_script = "upgrade.bat" if tasks.os.name == "nt" else "upgrade.sh"

    stable_decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(),
    )
    unstable_decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="unstable"),
        _repo_state(
            release_version=None, release_revision=None, remote_version="1.0.2"
        ),
    )

    assert stable_decision.apply is True
    assert Path(stable_decision.args[0]).name == expected_script
    assert stable_decision.args[1:] == [
        "--stable",
        "--target-version",
        "1.0.1",
        "--target-revision",
        "release-rev",
        "--target-tag",
        "v1.0.1",
    ]
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


def test_build_upgrade_decision_pins_stable_when_main_moved_after_release():
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(remote_revision="post-release-main-rev"),
    )

    assert decision.apply is True
    assert "--target-revision" in decision.args
    revision_index = decision.args.index("--target-revision") + 1
    assert decision.args[revision_index] == "release-rev"


def test_build_upgrade_decision_skips_release_channel_without_immutable_target():
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(release_revision=None),
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "release-target-missing"


def test_build_upgrade_decision_skips_when_recency_throttled(monkeypatch):
    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="unstable"),
        _repo_state(
            release_version=None, release_revision=None, remote_version="1.0.2"
        ),
        recency_throttled=True,
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "recency-throttled"


def test_execute_upgrade_decision_rechecks_recency_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tasks, "_auto_upgrade_ran_recently", lambda *_args, **_kwargs: True
    )

    log_messages: list[str] = []
    ensure_calls: list[tuple[bool, bool]] = []
    executed: list[bool] = []
    startup_called: list[bool] = []

    def _append_log(_base_dir, message):
        log_messages.append(message)

    def _ensure_runtime_services(
        _base_dir, restart_if_active, revert_on_failure, log_appender
    ):
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
    assert any(
        "last run was less than 60 minutes ago" in message for message in log_messages
    )


def test_execute_upgrade_decision_normalizes_batch_upgrade_command(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(tasks.os, "name", "posix")
    monkeypatch.setattr(
        tasks, "_auto_upgrade_ran_recently", lambda *_args, **_kwargs: False
    )

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


def test_broadcast_upgrade_start_message_skips_netmessage_schema_drift(
    monkeypatch, caplog
):
    from apps.nodes.models import NetMessage

    def _raise_schema_drift(**_kwargs):
        raise OperationalError("table nodes_netmessage has no column named kind")

    monkeypatch.setattr(tasks, "_resolve_upgrade_subject", lambda: "Upgrade node")
    monkeypatch.setattr(NetMessage, "broadcast", _raise_schema_drift)

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        tasks._broadcast_upgrade_start_message("local-rev", "remote-rev")

    assert (
        "Skipping auto-upgrade start Net Message because the NetMessage "
        "schema is not migrated yet"
    ) in caplog.text
    assert "Failed to broadcast auto-upgrade start Net Message" not in caplog.text
    assert "nodes_netmessage" not in caplog.text
    assert "kind" not in caplog.text
    assert "Traceback" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_broadcast_upgrade_start_message_logs_unexpected_failures(monkeypatch, caplog):
    from apps.nodes.models import NetMessage

    def _raise_runtime_error(**_kwargs):
        raise RuntimeError("broadcast offline")

    monkeypatch.setattr(tasks, "_resolve_upgrade_subject", lambda: "Upgrade node")
    monkeypatch.setattr(NetMessage, "broadcast", _raise_runtime_error)

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        tasks._broadcast_upgrade_start_message("local-rev", "remote-rev")

    records = [
        record
        for record in caplog.records
        if record.message == "Failed to broadcast auto-upgrade start Net Message"
    ]
    assert len(records) == 1
    assert records[0].exc_info is not None


def test_execute_upgrade_plan_records_pinned_target_and_broadcasts_target_revision(
    monkeypatch, tmp_path
):
    broadcasts: list[tuple[str, str]] = []
    timestamps: list[Path] = []
    ensure_calls: list[tuple[bool, bool]] = []

    monkeypatch.setattr(
        tasks,
        "_broadcast_upgrade_start_message",
        lambda local, remote: broadcasts.append((local, remote)),
    )
    monkeypatch.setattr(
        tasks,
        "_record_auto_upgrade_timestamp",
        lambda base_dir: timestamps.append(base_dir),
    )

    def _ensure_runtime_services(
        _base_dir, restart_if_active, revert_on_failure, log_appender
    ):
        assert log_appender is tasks.append_auto_upgrade_log
        ensure_calls.append((restart_if_active, revert_on_failure))
        return True

    ops = tasks.AutoUpgradeOperations(
        git_fetch=lambda *_args, **_kwargs: None,
        resolve_remote_revision=lambda *_args, **_kwargs: "rev",
        ensure_runtime_services=_ensure_runtime_services,
        delegate_upgrade=lambda *_args, **_kwargs: None,
        run_upgrade_command=lambda *_args, **_kwargs: (None, True),
    )
    args = [
        "./upgrade.sh",
        "--stable",
        "--target-version",
        "1.0.1",
        "--target-revision",
        "release-rev",
        "--target-tag",
        "v1.0.1",
    ]

    tasks._execute_upgrade_plan(
        tmp_path,
        _mode(mode="stable"),
        _repo_state(remote_revision="post-release-main-rev"),
        args,
        True,
        tmp_path / "auto-upgrade.log",
        ops,
        tasks.AutoUpgradeState(),
    )

    target_lock = tmp_path / ".locks" / "auto_upgrade_target.json"
    payload = json.loads(target_lock.read_text(encoding="utf-8"))

    assert payload["channel"] == "stable"
    assert payload["source"] == "auto-upgrade"
    assert payload["timestamp"]
    assert payload["target_version"] == "1.0.1"
    assert payload["target_revision"] == "release-rev"
    assert payload["target_tag"] == "v1.0.1"
    assert payload["observed_branch_revision"] == "post-release-main-rev"
    assert broadcasts == [("local-rev", "release-rev")]
    assert timestamps == [tmp_path]
    assert ensure_calls == [(True, True)]


def test_handle_failed_health_check_marks_revision_for_skip_and_manual_followup(
    monkeypatch, tmp_path
):
    failures: list[tuple[Path, str]] = []

    monkeypatch.setattr(tasks, "_current_revision", lambda _base_dir: "failed-rev")
    monkeypatch.setattr(
        tasks,
        "_record_auto_upgrade_failure",
        lambda base_dir, detail: failures.append((base_dir, detail)),
    )

    tasks._handle_failed_health_check(tmp_path, "returned HTTP 500")

    skip_lock = tmp_path / ".locks" / "auto_upgrade_skip_revisions.lck"
    assert skip_lock.read_text(encoding="utf-8").splitlines() == ["failed-rev"]

    log_text = (tmp_path / "logs" / "auto-upgrade.log").read_text(encoding="utf-8")
    assert "Recorded blocked revision failed-rev for auto-upgrade" in log_text
    assert (
        "Health check failed; marked current revision to be skipped; manual "
        "intervention required"
    ) in log_text
    assert "revert" not in log_text.lower()
    assert failures == [(tmp_path, "returned HTTP 500")]


def test_verify_auto_upgrade_health_docstring_does_not_promise_revert():
    doc = " ".join((tasks.verify_auto_upgrade_health.__doc__ or "").lower().split())

    assert "automatic revert" not in doc
    assert "skip" in doc
    assert "manual intervention" in doc
