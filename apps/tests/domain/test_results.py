from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from django.db import connections
from django.test.utils import override_settings

from apps.tests.models import TestResult


@dataclass
class RecordedTestResult:
    node_id: str
    name: str
    status: str
    duration: float | None
    log: str


def persist_results(
    results: Iterable[RecordedTestResult], database_settings: Mapping[str, Any]
) -> None:
    """Persist a collection of test results into the primary database.

    Parameters
    ----------
    results:
        Iterable of recorded results to persist.
    database_settings:
        Database configuration for the primary database. This ensures we do not
        write into the temporary test database created by pytest.
    """
    normalized_settings = {"default": dict(database_settings)}

    with override_settings(DATABASES=normalized_settings):
        connections.close_all()
        manager = TestResult.objects.using("default")
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
