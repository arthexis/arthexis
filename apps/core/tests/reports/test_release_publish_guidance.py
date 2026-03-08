from apps.core.views.reports.release_publish.pipeline import build_release_guidance


def test_build_release_guidance_for_github_actions_pause():
    guidance = build_release_guidance(
        done=False,
        error=None,
        started=True,
        paused=True,
        publish_pending=True,
        github_token_required=False,
        step_count=6,
        total_steps=9,
    )

    assert guidance["tone"] == "warning"
    assert guidance["title"] == "Waiting for GitHub Actions"


def test_build_release_guidance_for_running_state():
    guidance = build_release_guidance(
        done=False,
        error=None,
        started=True,
        paused=False,
        publish_pending=False,
        github_token_required=False,
        step_count=2,
        total_steps=9,
    )

    assert guidance["tone"] == "info"
    assert guidance["title"] == "Publishing in progress"
    assert guidance["message"] == "Step 3 of 9 is running."
