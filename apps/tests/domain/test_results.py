"""Utilities for recording pytest outcomes in the ``apps.tests`` models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from apps.tests.models import TestResult


@dataclass(frozen=True, slots=True)
class RecordedTestResult:
    """Serializable payload representing one pytest result entry."""

    node_id: str
    name: str
    status: str
    duration: float | None
    log: str = ""


def persist_results(
    results: Iterable[RecordedTestResult], *, use_permanent_db: bool = False
) -> int:
    """Persist test results to the database.

    When ``use_permanent_db`` is false, previous rows are removed before inserting
    the latest session's results.
    """

    payload = list(results)
    if not payload:
        return 0

    queryset = TestResult.objects
    if not use_permanent_db:
        queryset.all().delete()

    queryset.bulk_create(
        [
            TestResult(
                node_id=result.node_id,
                name=result.name,
                status=result.status,
                duration=result.duration,
                log=result.log,
            )
            for result in payload
        ]
    )
    return len(payload)
