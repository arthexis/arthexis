#!/usr/bin/env python
"""Shared migration reconciliation primitives."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ReconcileReport:
    """Summary of copied and skipped records during reconciliation."""

    backend: str
    copied_tables: list[str]
    missing_in_source: list[str]
    missing_in_target: list[str]
    skipped_tables: dict[str, str]
    skipped_columns: dict[str, list[str]] = field(default_factory=dict)
    skipped_rows: dict[str, int] = field(default_factory=dict)
