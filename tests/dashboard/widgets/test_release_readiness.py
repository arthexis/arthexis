"""Tests for release readiness score and checklist transitions."""

from dashboard.widgets.release_readiness import (
    BlockerTicket,
    ChecklistItem,
    summarize_release_readiness,
)


def test_readiness_score_and_no_go_with_unresolved_blockers_and_missing_approvals():
    """Score should degrade and status should be no-go when release criteria are missing."""

    summary = summarize_release_readiness(
        current_release="v1.2.3",
        tickets=[
            BlockerTicket(
                key="OPS-10",
                title="Rollback automation broken",
                url="https://example.invalid/OPS-10",
                status="open",
                tags=("blocker", "v1.2.3"),
            ),
            BlockerTicket(
                key="OPS-11",
                title="Non-blocker issue",
                url="https://example.invalid/OPS-11",
                status="open",
                tags=("v1.2.3",),
            ),
        ],
        checklist_items=[
            ChecklistItem(name="DB migrations verified", owner="SRE", verified=True),
            ChecklistItem(
                name="Comms sent",
                owner="Comms",
                verified=False,
            ),
            ChecklistItem(
                name="Rollback plan approved",
                owner="Release Manager",
                verified=True,
                requires_approval=True,
                approved=False,
            ),
        ],
        blocker_list_url="/admin/issues/?tag=blocker",
        checklist_admin_url="/admin/release/checklistitem/",
    )

    assert summary.go_no_go == "no-go"
    assert summary.readiness_score == 26
    assert len(summary.unresolved_blockers) == 1
    assert len(summary.missing_approvals) == 1
    assert summary.links["blockers"] == "/admin/issues/?tag=blocker"
    assert summary.links["release_checklist_admin"] == "/admin/release/checklistitem/"
