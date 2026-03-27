from apps.core.views.reports.release_publish.github_ops import poll_workflow_completion


def test_poll_workflow_completion_returns_completed_run_before_timeout():
    timeline = iter([
        None,
        {"status": "in_progress"},
        {"status": "completed", "conclusion": "success"},
    ])
    now = {"value": 0.0}

    def fetch_run():
        return next(timeline)

    def monotonic():
        return now["value"]

    def sleep(seconds):
        now["value"] += seconds

    run = poll_workflow_completion(
        fetch_run=fetch_run,
        timeout_seconds=10,
        interval_seconds=1,
        monotonic=monotonic,
        sleep=sleep,
    )
    assert run and run["conclusion"] == "success"
