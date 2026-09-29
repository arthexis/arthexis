"""Classify Watchtower observations without promoting intermediate states to incidents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchtowerObservation:
    conclusion: str | None
    successor_exists: bool = False
    stage_retrying: bool = False
    deployment_accepted: bool = False
    release_requested: bool = False
    publisher_conclusion: str | None = None
    evidence_conflict: bool = False


def classify(observation: WatchtowerObservation) -> str:
    """Return normal, pending, superseded, or actionable."""
    if observation.evidence_conflict:
        return "actionable"

    if observation.stage_retrying:
        return "pending"

    if observation.conclusion in {None, "", "queued", "in_progress", "waiting", "pending"}:
        return "pending"

    if observation.conclusion == "cancelled":
        return "superseded" if observation.successor_exists else "actionable"

    if observation.conclusion not in {"success", "skipped", "neutral"}:
        return "actionable"

    if not observation.deployment_accepted:
        return "normal" if observation.conclusion in {"skipped", "neutral"} else "pending"

    if not observation.release_requested:
        return "normal"

    if observation.publisher_conclusion in {None, "", "queued", "in_progress", "waiting", "pending"}:
        return "pending"
    if observation.publisher_conclusion == "success":
        return "normal"
    if observation.publisher_conclusion == "cancelled" and observation.successor_exists:
        return "superseded"
    return "actionable"
