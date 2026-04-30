from dashboard.widgets import BlockerTicket, ChecklistItem, summarize_release_readiness


def test_readiness_treats_all_pr_tickets_as_blockers_without_tags() -> None:
    summary = summarize_release_readiness(
        current_release="2026.04",
        tickets=[
            BlockerTicket(
                key="PR-101",
                title="Fix critical charging regression",
                url="https://example.test/pr/101",
                status="open",
                tags=(),
            )
        ],
        checklist_items=[ChecklistItem(name="Smoke test", owner="QA", verified=True)],
        blocker_list_url="https://example.test/blockers",
        checklist_admin_url="https://example.test/checklist",
    )

    assert summary.go_no_go == "no-go"
    assert len(summary.unresolved_blockers) == 1


def test_readiness_ignores_resolved_pr_tickets_even_without_tags() -> None:
    summary = summarize_release_readiness(
        current_release="2026.04",
        tickets=[
            BlockerTicket(
                key="PR-102",
                title="Update integration hooks",
                url="https://example.test/pr/102",
                status="closed",
                tags=(),
            )
        ],
        checklist_items=[ChecklistItem(name="Smoke test", owner="QA", verified=True)],
        blocker_list_url="https://example.test/blockers",
        checklist_admin_url="https://example.test/checklist",
    )

    assert summary.go_no_go == "go"
    assert summary.unresolved_blockers == ()
