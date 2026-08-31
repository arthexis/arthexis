from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.skills.codex_freeze import local_freeze_state, write_cached_state
from apps.skills.models import CodexFeatureFreeze, CodexTokenBudget


@dataclass(frozen=True)
class BudgetMonitorResult:
    budget: CodexTokenBudget
    status: str
    usage_percent: float
    freeze: CodexFeatureFreeze | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "budget": self.budget.name,
            "status": self.status,
            "used_tokens": self.budget.observed_token_count,
            "token_limit": self.budget.token_limit,
            "usage_percent": round(self.usage_percent, 2),
            "freeze_id": self.freeze.pk if self.freeze else None,
            "reason": self.reason,
        }


def sync_default_budget_from_environment() -> CodexTokenBudget | None:
    used = _positive_int_from_env("ARTHEXIS_CODEX_USED_TOKENS")
    limit = _positive_int_from_env("ARTHEXIS_CODEX_TOKEN_LIMIT")
    if used is None and limit is None:
        return None
    budget, _ = CodexTokenBudget.objects.get_or_create(name="default")
    if used is not None:
        budget.observed_token_count = used
    if limit is not None:
        budget.token_limit = limit
    reset_at = _datetime_from_env("ARTHEXIS_CODEX_RESET_AT")
    if reset_at is not None:
        budget.reset_at = reset_at
    budget.save()
    return budget


def monitor_codex_token_budgets() -> list[BudgetMonitorResult]:
    sync_default_budget_from_environment()
    return [evaluate_budget(budget) for budget in CodexTokenBudget.objects.all()]


def evaluate_budget(
    budget: CodexTokenBudget,
    *,
    now=None,
) -> BudgetMonitorResult:
    now = now or timezone.now()
    usage_percent = budget.usage_percent
    freeze = None
    reason = ""
    status = CodexTokenBudget.Status.OK

    if not budget.enabled or budget.token_limit <= 0:
        status = CodexTokenBudget.Status.DISABLED
    elif usage_percent >= budget.freeze_threshold_percent:
        status = CodexTokenBudget.Status.FROZEN
        reason = _freeze_reason(budget, usage_percent)
        if budget.auto_freeze:
            freeze = _ensure_budget_freeze(budget, reason=reason, now=now)
    elif usage_percent >= budget.warning_threshold_percent:
        status = CodexTokenBudget.Status.WARNING
        reason = _warning_reason(budget, usage_percent)

    budget.last_status = status
    budget.last_checked_at = now
    if freeze is not None:
        budget.last_freeze = freeze
        write_cached_state(local_freeze_state())
    budget.save(
        update_fields=[
            "last_status",
            "last_checked_at",
            "last_freeze",
            "updated_at",
        ]
    )
    return BudgetMonitorResult(
        budget=budget,
        status=status,
        usage_percent=usage_percent,
        freeze=freeze,
        reason=reason,
    )


def _ensure_budget_freeze(
    budget: CodexTokenBudget,
    *,
    reason: str,
    now,
) -> CodexFeatureFreeze:
    ends_at = budget.reset_at
    if ends_at is None or ends_at <= now:
        ends_at = now + timedelta(hours=budget.freeze_duration_hours)
    for _ in range(3):
        current = CodexFeatureFreeze.current()
        if current is not None:
            return current
        try:
            return CodexFeatureFreeze.create_with_next_policy_version(
                reason=reason,
                starts_at=now,
                ends_at=ends_at,
                metadata={"token_budget": budget.name},
            )
        except IntegrityError:
            current = CodexFeatureFreeze.current()
            if current is not None:
                return current
    raise IntegrityError("Could not allocate Codex freeze policy version.")


def _freeze_reason(budget: CodexTokenBudget, usage_percent: float) -> str:
    return (
        f"Codex token budget {budget.name} reached freeze threshold: "
        f"{budget.observed_token_count}/{budget.token_limit} tokens "
        f"({usage_percent:.1f}%)."
    )


def _warning_reason(budget: CodexTokenBudget, usage_percent: float) -> str:
    return (
        f"Codex token budget {budget.name} reached warning threshold: "
        f"{budget.observed_token_count}/{budget.token_limit} tokens "
        f"({usage_percent:.1f}%)."
    )


def _positive_int_from_env(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return max(parsed, 0)


def _datetime_from_env(name: str):
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
