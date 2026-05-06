from celery import shared_task

from apps.tasks.tasks import report_exception_to_github


@shared_task(name="apps.repos.tasks.monitor_github_readiness")
def monitor_github_readiness() -> dict[str, object]:
    """Poll configured GitHub readiness signals and maintain the operator queue."""

    from apps.repos import github_monitor

    try:
        return github_monitor.run_monitor_cycle(launch=True)
    except Exception as exc:
        github_monitor.notify_admins_of_failure(
            "Arthexis GitHub monitor failed",
            f"The GitHub monitor task failed before completing a cycle.\n\n{exc}",
        )
        raise


__all__ = ["monitor_github_readiness", "report_exception_to_github"]
