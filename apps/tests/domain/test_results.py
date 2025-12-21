from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import connections
from django.db.utils import DEFAULT_DB_ALIAS

from apps.tests.models import TestResult


@dataclass
class RecordedTestResult:
    node_id: str
    name: str
    status: str
    duration: float | None
    log: str


def persist_results(results: Iterable[RecordedTestResult]) -> None:
    """Persist a collection of test results into the active database."""
    connection = connections[DEFAULT_DB_ALIAS]
    if TestResult._meta.db_table not in connection.introspection.table_names():
        return

    manager = TestResult.objects.using(DEFAULT_DB_ALIAS)
    manager.all().delete()
    manager.bulk_create(
        [
            TestResult(
                node_id=result.node_id,
                name=result.name,
                status=result.status,
                duration=result.duration,
                log=result.log,
            )
            for result in results
        ]
    )
