from dashboard.widgets import BlockerTicket, ChecklistItem, summarize_release_readiness


def test_readiness_counts_current_release_tickets_without_blocker_tag() -> None:
    summary = summarize_release_readiness(
        current_release="2026.04",
        tickets=[
            BlockerTicket(
                key="PR-101",
                title="Fix critical charging regression",
                url="https://example.test/pr/101",
                status="open",
                tags=("2026.04",),
            )
        ],
        checklist_items=[ChecklistItem(name="Smoke test", owner="QA", verified=True)],
        blocker_list_url="https://example.test/blockers",
        checklist_admin_url="https://example.test/checklist",
    )

    assert summary.go_no_go == "no-go"
    assert len(summary.unresolved_blockers) == 1


def test_readiness_ignores_resolved_current_release_tickets() -> None:
    summary = summarize_release_readiness(
        current_release="2026.04",
        tickets=[
            BlockerTicket(
                key="PR-102",
                title="Update integration hooks",
                url="https://example.test/pr/102",
                status="closed",
                tags=("2026.04",),
            )
        ],
        checklist_items=[ChecklistItem(name="Smoke test", owner="QA", verified=True)],
        blocker_list_url="https://example.test/blockers",
        checklist_admin_url="https://example.test/checklist",
    )

    assert summary.go_no_go == "go"
    assert summary.unresolved_blockers == ()


def test_readiness_ignores_unresolved_tickets_for_other_releases() -> None:
    summary = summarize_release_readiness(
        current_release="2026.04",
        tickets=[
            BlockerTicket(
                key="PR-099",
                title="Fix prior release packaging",
                url="https://example.test/pr/99",
                status="open",
                tags=("2026.03",),
            )
        ],
        checklist_items=[ChecklistItem(name="Smoke test", owner="QA", verified=True)],
        blocker_list_url="https://example.test/blockers",
        checklist_admin_url="https://example.test/checklist",
    )

    assert summary.go_no_go == "go"
    assert summary.unresolved_blockers == ()
