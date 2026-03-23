"""Release readiness widget logic.

This module computes release go-live readiness from blocker tickets and
checklist/approval state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


_DONE_STATUSES = frozenset({"closed", "done", "resolved"})


@dataclass(frozen=True, slots=True)
class BlockerTicket:
    """Represent a ticket that can block a release."""

    key: str
    title: str
    url: str
    status: str
    tags: tuple[str, ...]

    @property
    def is_resolved(self) -> bool:
        """Return whether the ticket status is considered done."""

        return self.status.strip().lower() in _DONE_STATUSES


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    """Represent one release checklist row."""

    name: str
    owner: str
    verified: bool = False
    requires_approval: bool = False
    approved: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessSummary:
    """Computed release readiness view model for dashboard rendering."""

    readiness_score: int
    go_no_go: str
    rationale: str
    unresolved_blockers: tuple[BlockerTicket, ...]
    missing_approvals: tuple[ChecklistItem, ...]
    links: dict[str, str]


class ChecklistTransitionError(ValueError):
    """Raised when an invalid checklist state transition is requested."""


def transition_checklist_item(
    item: ChecklistItem,
    *,
    verified: bool | None = None,
    approved: bool | None = None,
) -> ChecklistItem:
    """Return a new checklist item after applying a valid state transition."""

    updated = replace(item)

    if verified is not None:
        updated = replace(updated, verified=verified)

    if approved is not None:
        if not updated.requires_approval and approved:
            raise ChecklistTransitionError(
                f"Checklist item '{updated.name}' does not require approval."
            )
        if approved and not updated.verified:
            raise ChecklistTransitionError(
                f"Checklist item '{updated.name}' must be verified before approval."
            )
        updated = replace(updated, approved=approved)

    if not updated.verified and updated.approved:
        raise ChecklistTransitionError(
            f"Checklist item '{updated.name}' cannot be approved while unverified."
        )

    return updated


def summarize_release_readiness(
    *,
    current_release: str,
    tickets: list[BlockerTicket],
    checklist_items: list[ChecklistItem],
    blocker_list_url: str,
    checklist_admin_url: str,
) -> ReadinessSummary:
    """Build a go-live readiness summary for the current release."""

    tagged_blockers = [
        ticket
        for ticket in tickets
        if current_release in ticket.tags and "blocker" in ticket.tags
    ]
    unresolved_blockers = tuple(ticket for ticket in tagged_blockers if not ticket.is_resolved)

    missing_approvals = tuple(
        item for item in checklist_items if item.requires_approval and not item.approved
    )
    unchecked_items = tuple(item for item in checklist_items if not item.verified)

    checklist_total = len(checklist_items)
    checklist_verified = checklist_total - len(unchecked_items)
    checklist_score = 100 if checklist_total == 0 else int((checklist_verified / checklist_total) * 100)

    approval_required = len([item for item in checklist_items if item.requires_approval])
    approval_complete = approval_required - len(missing_approvals)
    approval_score = 100 if approval_required == 0 else int((approval_complete / approval_required) * 100)

    blocker_score = 100 if not unresolved_blockers else 0

    readiness_score = int((checklist_score * 0.4) + (approval_score * 0.3) + (blocker_score * 0.3))

    is_go = not unresolved_blockers and not unchecked_items and not missing_approvals
    go_no_go = "go" if is_go else "no-go"

    reasons: list[str] = []
    if unresolved_blockers:
        reasons.append(f"{len(unresolved_blockers)} unresolved blocker ticket(s)")
    if unchecked_items:
        reasons.append(f"{len(unchecked_items)} checklist item(s) not verified")
    if missing_approvals:
        owners = ", ".join(sorted({item.owner for item in missing_approvals if item.owner}))
        owner_suffix = f" (owners: {owners})" if owners else ""
        reasons.append(
            f"{len(missing_approvals)} approval(s) missing{owner_suffix}"
        )

    rationale = "Ready for release." if is_go else "; ".join(reasons)

    links = {
        "blockers": blocker_list_url,
        "release_checklist_admin": checklist_admin_url,
    }

    return ReadinessSummary(
        readiness_score=readiness_score,
        go_no_go=go_no_go,
        rationale=rationale,
        unresolved_blockers=unresolved_blockers,
        missing_approvals=missing_approvals,
        links=links,
    )
