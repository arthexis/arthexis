from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

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


def _resolve_results_connection_alias(use_permanent_db: bool) -> str:
    if not use_permanent_db:
        return DEFAULT_DB_ALIAS

    default_connection = connections[DEFAULT_DB_ALIAS]
    persistent_config = dict(settings.DATABASES[DEFAULT_DB_ALIAS])
    persistent_name = persistent_config.get("NAME")
    if not persistent_name or persistent_name == default_connection.settings_dict.get("NAME"):
        return DEFAULT_DB_ALIAS

    alias = "persistent_results"
    if alias not in connections.databases:
        connections.databases[alias] = persistent_config
    return alias


def persist_results(
    results: Iterable[RecordedTestResult], *, use_permanent_db: bool = False
) -> None:
    """Persist a collection of test results into the active database."""
    alias = _resolve_results_connection_alias(use_permanent_db)
    connection = connections[alias]
    if TestResult._meta.db_table not in connection.introspection.table_names():
        return

    manager = TestResult.objects.using(alias)
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
