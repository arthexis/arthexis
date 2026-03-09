"""Tests for release readiness score and checklist transitions."""

import pytest

from dashboard.widgets.release_readiness import (
    BlockerTicket,
    ChecklistItem,
    ChecklistTransitionError,
    summarize_release_readiness,
    transition_checklist_item,
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


def test_readiness_score_go_when_all_criteria_are_met():
    """Score should be 100 and status should be go when all criteria are satisfied."""

    summary = summarize_release_readiness(
        current_release="v1.2.3",
        tickets=[
            BlockerTicket(
                key="OPS-12",
                title="Blocking issue fixed",
                url="https://example.invalid/OPS-12",
                status="closed",
                tags=("blocker", "v1.2.3"),
            ),
        ],
        checklist_items=[
            ChecklistItem(name="DB migrations verified", owner="SRE", verified=True),
            ChecklistItem(name="Comms sent", owner="Comms", verified=True),
            ChecklistItem(
                name="Rollback plan approved",
                owner="Release Manager",
                verified=True,
                requires_approval=True,
                approved=True,
            ),
        ],
        blocker_list_url="/admin/issues/?tag=blocker",
        checklist_admin_url="/admin/release/checklistitem/",
    )

    assert summary.readiness_score == 100
    assert summary.go_no_go == "go"
    assert summary.rationale == "Ready for release."


def test_checklist_transition_rejects_approval_before_verification():
    """Checklist transition should enforce verify-before-approve."""

    item = ChecklistItem(
        name="Rollback plan approved",
        owner="Release Manager",
        verified=False,
        requires_approval=True,
        approved=False,
    )

    with pytest.raises(ChecklistTransitionError):
        transition_checklist_item(item, approved=True)


def test_checklist_transition_allows_verified_then_approved_state():
    """Checklist transition should permit valid verified -> approved sequence."""

    item = ChecklistItem(
        name="Rollback plan approved",
        owner="Release Manager",
        verified=False,
        requires_approval=True,
        approved=False,
    )

    verified = transition_checklist_item(item, verified=True)
    approved = transition_checklist_item(verified, approved=True)

    assert approved.verified is True
    assert approved.approved is True
