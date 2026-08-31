from celery import shared_task

from apps.skills.codex_budget import monitor_codex_token_budgets


@shared_task(name="apps.skills.tasks.monitor_codex_token_budgets")
def monitor_codex_token_budgets_task() -> list[dict[str, object]]:
    return [result.as_dict() for result in monitor_codex_token_budgets()]
