from pathlib import Path

from apps.core.tasks.auto_upgrade import tasks


def _mode(**overrides):
    defaults = {
        "mode": "stable",
        "admin_override": False,
        "override_log": None,
        "mode_file_exists": True,
        "mode_file_physical": True,
        "interval_minutes": 60,
        "requires_canaries": False,
        "requires_pypi": False,
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

    monkeypatch.setattr(tasks, "_canary_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tasks, "_get_package_release_model", lambda: _ReleaseModel)

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
    assert stable_decision.args == ["./upgrade.sh", "--stable"]
    assert unstable_decision.apply is True
    assert unstable_decision.args == ["./upgrade.sh", "--latest"]


def test_build_upgrade_decision_skips_when_pypi_gate_blocks(monkeypatch):
    monkeypatch.setattr(tasks, "_canary_gate", lambda *_args, **_kwargs: True)

    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(requires_pypi=True),
        _repo_state(release_pypi_url=None),
    )

    assert decision.skip is True
    assert decision.reason == "pypi-release-missing"


def test_build_upgrade_decision_skips_when_canary_gate_blocks(monkeypatch):
    monkeypatch.setattr(tasks, "_canary_gate", lambda *_args, **_kwargs: False)

    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(requires_canaries=True),
        _repo_state(),
    )

    assert decision.skip is True
    assert decision.reason == "canary-gate-blocked"


def test_build_upgrade_decision_skips_stable_when_release_revision_mismatches(monkeypatch):
    class _ReleaseModel:
        @staticmethod
        def matches_revision(_version, _revision):
            return False

    monkeypatch.setattr(tasks, "_canary_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tasks, "_get_package_release_model", lambda: _ReleaseModel)

    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="stable"),
        _repo_state(),
    )

    assert decision.skip is True
    assert decision.reason == "release-revision-mismatch"


def test_build_upgrade_decision_skips_when_recency_throttled(monkeypatch):
    monkeypatch.setattr(tasks, "_canary_gate", lambda *_args, **_kwargs: True)

    decision = tasks.build_upgrade_decision(
        Path("/tmp/base"),
        _mode(mode="unstable"),
        _repo_state(release_version=None, release_revision=None, remote_version="1.0.2"),
        recency_throttled=True,
    )

    assert decision.skip is True
    assert decision.apply is False
    assert decision.reason == "recency-throttled"
