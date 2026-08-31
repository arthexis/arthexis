from dashboard.widgets import BlockerTicket, ChecklistItem, summarize_release_readiness


def test_release_readiness_blocker_ticket_classification() -> None:
    cases = [
        (
            BlockerTicket(
                key="PR-101",
                title="Fix critical charging regression",
                url="https://example.test/pr/101",
                status="open",
                tags=("2026.04",),
            ),
            "no-go",
            1,
        ),
        (
            BlockerTicket(
                key="PR-102",
                title="Update integration hooks",
                url="https://example.test/pr/102",
                status="closed",
                tags=("2026.04",),
            ),
            "go",
            0,
        ),
        (
            BlockerTicket(
                key="PR-099",
                title="Fix prior release packaging",
                url="https://example.test/pr/99",
                status="open",
                tags=("2026.03",),
            ),
            "go",
            0,
        ),
        (
            BlockerTicket(
                key="PR-103",
                title="Investigate intermittent charger disconnects",
                url="https://example.test/pr/103",
                status="open",
                tags=(),
            ),
            "no-go",
            1,
        ),
    ]

    for ticket, expected_go_no_go, expected_blockers in cases:
        summary = summarize_release_readiness(
            current_release="2026.04",
            tickets=[ticket],
            checklist_items=[
                ChecklistItem(name="Smoke test", owner="QA", verified=True)
            ],
            blocker_list_url="https://example.test/blockers",
            checklist_admin_url="https://example.test/checklist",
        )

        assert summary.go_no_go == expected_go_no_go
        assert len(summary.unresolved_blockers) == expected_blockers
