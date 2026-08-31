from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.skills.codex_budget import evaluate_budget, monitor_codex_token_budgets
from apps.skills.models import CodexFeatureFreeze, CodexTokenBudget

pytestmark = [pytest.mark.django_db]


def test_budget_warning_threshold_records_warning_without_freeze():
    budget = CodexTokenBudget.objects.create(
        name="default",
        token_limit=1000,
        observed_token_count=850,
        warning_threshold_percent=80,
        freeze_threshold_percent=90,
    )

    result = evaluate_budget(budget)

    budget.refresh_from_db()
    assert result.status == CodexTokenBudget.Status.WARNING
    assert result.freeze is None
    assert budget.last_status == CodexTokenBudget.Status.WARNING
    assert CodexFeatureFreeze.objects.count() == 0


def test_budget_freeze_threshold_creates_freeze_and_cache(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    reset_at = timezone.now() + timedelta(hours=6)
    budget = CodexTokenBudget.objects.create(
        name="default",
        token_limit=1000,
        observed_token_count=950,
        warning_threshold_percent=80,
        freeze_threshold_percent=90,
        reset_at=reset_at,
    )

    result = evaluate_budget(budget)

    budget.refresh_from_db()
    freeze = CodexFeatureFreeze.objects.get()
    assert result.status == CodexTokenBudget.Status.FROZEN
    assert result.freeze == freeze
    assert budget.last_freeze == freeze
    assert freeze.ends_at == reset_at
    assert "950/1000" in freeze.reason
    cache_payload = json.loads(
        (tmp_path / "work" / "codex" / "freeze-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert cache_payload["active"] is True


def test_monitor_syncs_default_budget_from_environment(monkeypatch):
    reset_at = (timezone.now() + timedelta(hours=2)).isoformat()
    monkeypatch.setenv("ARTHEXIS_CODEX_USED_TOKENS", "91")
    monkeypatch.setenv("ARTHEXIS_CODEX_TOKEN_LIMIT", "100")
    monkeypatch.setenv("ARTHEXIS_CODEX_RESET_AT", reset_at)

    results = monitor_codex_token_budgets()

    budget = CodexTokenBudget.objects.get(name="default")
    assert len(results) == 1
    assert budget.observed_token_count == 91
    assert budget.token_limit == 100
    assert budget.last_status == CodexTokenBudget.Status.FROZEN


def test_codex_freeze_monitor_command_reports_budget():
    CodexTokenBudget.objects.create(
        name="default",
        token_limit=1000,
        observed_token_count=850,
        warning_threshold_percent=80,
        freeze_threshold_percent=90,
    )
    stdout = StringIO()

    call_command("codex_freeze", "monitor", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["budgets"][0]["budget"] == "default"
    assert payload["budgets"][0]["status"] == CodexTokenBudget.Status.WARNING


def test_token_budget_db_rejects_warning_threshold_at_or_above_freeze():
    with pytest.raises(IntegrityError), transaction.atomic():
        CodexTokenBudget.objects.create(
            name="invalid-order",
            token_limit=1000,
            warning_threshold_percent=90,
            freeze_threshold_percent=90,
        )


def test_token_budget_db_rejects_freeze_threshold_above_one_hundred():
    with pytest.raises(IntegrityError), transaction.atomic():
        CodexTokenBudget.objects.create(
            name="invalid-freeze",
            token_limit=1000,
            warning_threshold_percent=80,
            freeze_threshold_percent=101,
        )


def test_budget_freeze_returns_current_freeze_after_policy_version_collision(
    tmp_path,
    settings,
    monkeypatch,
):
    settings.BASE_DIR = tmp_path
    now = timezone.now()
    current = CodexFeatureFreeze.create_with_retry(
        reason="Existing freeze.",
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=1),
    )
    budget = CodexTokenBudget.objects.create(
        name="default",
        token_limit=1000,
        observed_token_count=950,
        warning_threshold_percent=80,
        freeze_threshold_percent=90,
    )
    original_current = CodexFeatureFreeze.current
    calls = {"create": 0, "current": 0}

    def current_after_collision():
        calls["current"] += 1
        if calls["current"] == 1:
            return None
        return original_current()

    def fail_create(**fields):
        calls["create"] += 1
        raise IntegrityError("simulated policy version collision")

    monkeypatch.setattr(CodexFeatureFreeze, "current", current_after_collision)
    monkeypatch.setattr(
        CodexFeatureFreeze,
        "create_with_next_policy_version",
        fail_create,
    )

    result = evaluate_budget(budget, now=now)

    assert result.freeze == current
    assert calls["create"] == 1
    assert CodexFeatureFreeze.objects.count() == 1
