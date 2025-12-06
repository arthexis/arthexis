from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from django.conf import settings
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
    normalized_settings = {DEFAULT_DB_ALIAS: dict(database_settings)}

    with _temporary_database_settings(normalized_settings):
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


@contextmanager
def _temporary_database_settings(database_settings: Mapping[str, Mapping[str, Any]]):
    """Swap database settings for the duration of the context."""

    original_settings = deepcopy(settings.DATABASES)
    connections.close_all()
    connections.databases.update(database_settings)

    try:
        yield
    finally:
        connections.close_all()
        connections.databases.clear()
        connections.databases.update(original_settings)
