import pytest

from apps.core.views.reports.release_publish.pipeline import build_release_guidance


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            {
                "done": True,
                "error": None,
                "started": True,
                "paused": False,
                "publish_pending": False,
                "github_token_required": False,
                "step_count": 9,
                "total_steps": 9,
            },
            {
                "tone": "success",
                "title": "Publish completed",
                "message": "All release steps finished successfully. You can now share the package URLs below.",
            },
        ),
        (
            {
                "done": False,
                "error": "boom",
                "started": True,
                "paused": True,
                "publish_pending": False,
                "github_token_required": False,
                "step_count": 3,
                "total_steps": 9,
            },
            {
                "tone": "error",
                "title": "Publish needs attention",
                "message": "Resolve the error below, then continue to retry the current step.",
            },
        ),
        (
            {
                "done": False,
                "error": None,
                "started": False,
                "paused": False,
                "publish_pending": False,
                "github_token_required": False,
                "step_count": 0,
                "total_steps": 9,
            },
            {
                "tone": "info",
                "title": "Ready to publish",
                "message": "Review credentials and click Start Publish when you are ready.",
            },
        ),
        (
            {
                "done": False,
                "error": None,
                "started": True,
                "paused": True,
                "publish_pending": False,
                "github_token_required": True,
                "step_count": 4,
                "total_steps": 9,
            },
            {
                "tone": "warning",
                "title": "GitHub token required",
                "message": "Publishing is paused until a GitHub token is provided for this session.",
            },
        ),
        (
            {
                "done": False,
                "error": None,
                "started": True,
                "paused": True,
                "publish_pending": True,
                "github_token_required": False,
                "step_count": 6,
                "total_steps": 9,
            },
            {
                "tone": "warning",
                "title": "Waiting for GitHub Actions",
                "message": "The publish workflow is still running on GitHub. This page will keep checking automatically.",
            },
        ),
        (
            {
                "done": False,
                "error": None,
                "started": True,
                "paused": True,
                "publish_pending": False,
                "github_token_required": False,
                "step_count": 5,
                "total_steps": 9,
            },
            {
                "tone": "warning",
                "title": "Publishing paused",
                "message": "Press Continue Publish to proceed from the current step.",
            },
        ),
        (
            {
                "done": False,
                "error": None,
                "started": True,
                "paused": False,
                "publish_pending": False,
                "github_token_required": False,
                "step_count": 2,
                "total_steps": 9,
            },
            {
                "tone": "info",
                "title": "Publishing in progress",
                "message": "Step 3 of 9 is running.",
            },
        ),
        (
            {
                "done": False,
                "error": None,
                "started": True,
                "paused": False,
                "publish_pending": False,
                "github_token_required": False,
                "step_count": 99,
                "total_steps": 9,
            },
            {
                "tone": "info",
                "title": "Publishing in progress",
                "message": "Step 9 of 9 is running.",
            },
        ),
    ],
)
def test_build_release_guidance_all_paths(state, expected):
    guidance = build_release_guidance(**state)

    assert guidance == expected
