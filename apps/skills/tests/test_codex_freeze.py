from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.skills.codex_freeze import check_freeze, decision_from_state, freeze_status
from apps.skills.models import CodexFeatureFreeze

pytestmark = [pytest.mark.django_db]


def _create_freeze(**fields) -> CodexFeatureFreeze:
    return CodexFeatureFreeze.create_with_retry(**fields)


def test_active_freeze_refuses_from_cache(tmp_path, settings, monkeypatch):
    settings.BASE_DIR = tmp_path
    now = timezone.now()
    state = {
        "active": True,
        "reason": "Token reserve below threshold.",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(),
        "policy_version": 7,
    }
    cache_file = tmp_path / "work" / "codex" / "freeze-state.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps(state), encoding="utf-8")

    decision = check_freeze(refresh=False)

    assert decision.decision == "refuse"
    assert "Token reserve below threshold" in decision.reason


def test_active_cache_can_be_lifted_by_remote_refresh(tmp_path, settings, monkeypatch):
    settings.BASE_DIR = tmp_path
    now = timezone.now()
    active_state = {
        "active": True,
        "reason": "Token reserve below threshold.",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(),
        "policy_version": 7,
        "generated_at": now.isoformat(),
        "max_age_seconds": 300,
    }
    remote_state = {
        "active": False,
        "reason": "",
        "starts_at": None,
        "ends_at": None,
        "policy_version": 8,
        "generated_at": now.isoformat(),
        "max_age_seconds": 300,
    }
    cache_file = tmp_path / "work" / "codex" / "freeze-state.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps(active_state), encoding="utf-8")
    monkeypatch.setattr(
        "apps.skills.codex_freeze.fetch_remote_state",
        lambda *args, **kwargs: remote_state,
    )

    decision = check_freeze(
        refresh=True,
        state_url="https://example.test/freeze-state.json",
        now=now,
    )

    assert decision.decision == "allow"
    assert decision.source == "remote"
    assert json.loads(cache_file.read_text(encoding="utf-8"))["active"] is False


def test_remote_failure_without_cache_allows(tmp_path, settings, monkeypatch):
    settings.BASE_DIR = tmp_path

    def fail_fetch(*args, **kwargs):
        raise TimeoutError("remote timeout")

    monkeypatch.setattr("apps.skills.codex_freeze.fetch_remote_state", fail_fetch)

    decision = check_freeze(
        refresh=True,
        state_url="https://example.test/freeze-state.json",
    )

    assert decision.decision == "allow"
    assert decision.source in {"cache-stale", "remote-error"}
    assert "remote timeout" in decision.error


def test_refresh_skips_remote_fetch_when_url_is_unconfigured(
    tmp_path, settings, monkeypatch
):
    settings.BASE_DIR = tmp_path

    def fail_fetch(*args, **kwargs):
        raise AssertionError("remote fetch should not run without a URL")

    monkeypatch.delenv("ARTHEXIS_CODEX_FREEZE_URL", raising=False)
    monkeypatch.setattr("apps.skills.codex_freeze.fetch_remote_state", fail_fetch)

    decision = check_freeze(refresh=True)

    assert decision.decision == "allow"
    assert decision.source == "cache"
    assert decision.error == ""


def test_expired_freeze_state_allows():
    now = timezone.now()
    state = {
        "active": True,
        "reason": "Expired freeze.",
        "starts_at": (now - timedelta(hours=2)).isoformat(),
        "ends_at": (now - timedelta(minutes=1)).isoformat(),
        "policy_version": 3,
    }

    decision = decision_from_state(state, source="cache", now=now)

    assert decision.decision == "allow"


def test_stale_freeze_state_ttl_allows():
    now = timezone.now()
    state = {
        "active": True,
        "reason": "Stale freeze.",
        "starts_at": (now - timedelta(minutes=10)).isoformat(),
        "ends_at": (now + timedelta(hours=1)).isoformat(),
        "policy_version": 4,
        "generated_at": (now - timedelta(minutes=10)).isoformat(),
        "max_age_seconds": 60,
    }

    decision = decision_from_state(state, source="cache", now=now)

    assert decision.decision == "allow"
    assert decision.source == "cache-stale"


def test_codex_freeze_check_hook_json_refuses_active_local_freeze(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    _create_freeze(
        reason="Manual token freeze.",
        starts_at=timezone.now() - timedelta(minutes=1),
        ends_at=timezone.now() + timedelta(hours=1),
    )
    stdout = StringIO()

    call_command(
        "codex_freeze",
        "check",
        "--hook-json",
        "--no-refresh",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["decision"] == "refuse"
    assert "Manual token freeze" in payload["reason"]


def test_freeze_state_endpoint_is_removed(client):
    _create_freeze(
        reason="Endpoint freeze secret.",
        starts_at=timezone.now() - timedelta(minutes=1),
        ends_at=timezone.now() + timedelta(hours=1),
    )

    response = client.get("/api/codex/freeze-state/")

    assert response.status_code == 404
    assert b"Endpoint freeze secret" not in response.content


def test_emergency_bypass_allows_active_freeze_and_reports_reason(monkeypatch):
    now = timezone.now()
    bypass_until = now + timedelta(minutes=30)
    state = {
        "active": True,
        "reason": "Budget freeze.",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(),
        "policy_version": 8,
    }
    monkeypatch.setenv(
        "ARTHEXIS_CODEX_FREEZE_BYPASS_UNTIL",
        bypass_until.isoformat(),
    )
    monkeypatch.setenv("ARTHEXIS_CODEX_FREEZE_BYPASS_REASON", "critical repair")

    decision = decision_from_state(state, source="cache", now=now)

    assert decision.decision == "allow"
    assert decision.source == "cache-emergency-bypass"
    assert decision.bypass_until == bypass_until
    assert "critical repair" in decision.reason


def test_freeze_status_includes_cache_remote_source_and_bypass(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    status = freeze_status()

    assert status["cache_path"].replace("\\", "/").endswith(
        "work/codex/freeze-state.json"
    )
    assert status["remote_url"] == ""
    assert status["decision"] == "allow"
    assert status["source"] == "cache"


@pytest.mark.parametrize(
    "args,match",
    [
        (("--hours", "0"), "--hours must be greater than 0"),
        (("--hours", "-1"), "--hours must be greater than 0"),
        (("--until", "0"), "--until hours value must be greater than 0"),
    ],
)
def test_codex_freeze_start_rejects_invalid_duration(args, match):
    with pytest.raises(CommandError, match=match):
        call_command("codex_freeze", "start", "--reason", "invalid window", *args)


def test_codex_freeze_start_rejects_past_until():
    past = (timezone.now() - timedelta(minutes=1)).isoformat()

    with pytest.raises(CommandError, match="--until must be in the future"):
        call_command("codex_freeze", "start", "--reason", "past window", "--until", past)


def test_codex_freeze_end_deactivates_future_freeze(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    _create_freeze(
        reason="Scheduled freeze.",
        starts_at=timezone.now() + timedelta(hours=1),
        ends_at=timezone.now() + timedelta(hours=2),
    )
    stdout = StringIO()

    call_command("codex_freeze", "end", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["ended"] == 1
    assert CodexFeatureFreeze.objects.filter(active=True).count() == 0


def test_codex_freeze_start_retries_policy_version_collision(monkeypatch):
    original = CodexFeatureFreeze.create_with_next_policy_version
    calls = {"count": 0}

    def collide_once(**fields):
        calls["count"] += 1
        if calls["count"] == 1:
            raise IntegrityError("simulated policy version collision")
        return original(**fields)

    monkeypatch.setattr(
        CodexFeatureFreeze,
        "create_with_next_policy_version",
        collide_once,
    )

    call_command(
        "codex_freeze",
        "start",
        "--reason",
        "collision retry",
        "--hours",
        "1",
    )

    assert calls["count"] == 2
    assert CodexFeatureFreeze.objects.get().policy_version == 1


def test_codex_feature_freeze_db_rejects_invalid_window():
    now = timezone.now()

    with pytest.raises(IntegrityError), transaction.atomic():
        CodexFeatureFreeze.objects.create(
            reason="Invalid window.",
            starts_at=now,
            ends_at=now - timedelta(minutes=1),
            policy_version=1,
        )


def test_codex_feature_freeze_clean_allows_missing_end_for_form_validation():
    freeze = CodexFeatureFreeze(reason="Incomplete form.")

    freeze.clean()
