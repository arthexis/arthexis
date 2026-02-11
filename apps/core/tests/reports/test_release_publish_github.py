from apps.core.views.reports.release_publish.integrations.github import (
    fetch_publish_workflow_run,
    poll_workflow_completion,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_publish_workflow_run_falls_back_to_head_sha_match():
    responses = iter([
        {
            "workflow_runs": [
                {"id": 1, "head_branch": "other", "head_sha": "zzz999"},
            ]
        },
        {
            "workflow_runs": [
                {"id": 2, "head_branch": "main", "head_sha": "abc123"},
            ]
        },
    ])
    captured_params = []

    def request(_method, _url, **kwargs):
        captured_params.append(kwargs.get("params"))
        return _Response(next(responses))

    run = fetch_publish_workflow_run(
        request=request,
        owner="acme",
        repo="widget",
        tag_name="v1.2.3",
        tag_sha="abc123",
        token="token",
    )

    assert run and run["id"] == 2
    assert captured_params == [
        {"event": "push", "branch": "v1.2.3", "per_page": 5},
        {"event": "push", "per_page": 20},
    ]


def test_fetch_publish_workflow_run_matches_head_sha_without_fallback():
    calls = []

    def request(_method, _url, **kwargs):
        calls.append(kwargs.get("params"))
        return _Response(
            {
                "workflow_runs": [
                    {"id": 9, "head_branch": "main", "head_sha": "abc123"},
                ]
            }
        )

    run = fetch_publish_workflow_run(
        request=request,
        owner="acme",
        repo="widget",
        tag_name="v1.2.3",
        tag_sha="abc123",
        token="token",
    )

    assert run and run["id"] == 9
    assert calls == [{"event": "push", "branch": "v1.2.3", "per_page": 5}]


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


def test_poll_workflow_completion_times_out():
    now = {"value": 0.0}

    def fetch_run():
        return {"status": "in_progress"}

    def monotonic():
        return now["value"]

    def sleep(seconds):
        now["value"] += seconds

    run = poll_workflow_completion(
        fetch_run=fetch_run,
        timeout_seconds=2,
        interval_seconds=1,
        monotonic=monotonic,
        sleep=sleep,
    )
    assert run is None


def test_poll_workflow_completion_rejects_non_positive_interval():
    def fetch_run():
        return None

    try:
        poll_workflow_completion(
            fetch_run=fetch_run,
            timeout_seconds=2,
            interval_seconds=0,
        )
    except ValueError as exc:
        assert "interval_seconds" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-positive interval")
