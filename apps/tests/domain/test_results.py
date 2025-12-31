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
    features: list[dict[str, str | None]] | None = None


def persist_results(results: Iterable[RecordedTestResult]) -> None:
    """Persist a collection of test results into the active database."""
    connection = connections[DEFAULT_DB_ALIAS]
    table_names = connection.introspection.table_names()
    if TestResult._meta.db_table not in table_names:
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

    try:
        from apps.release.models import Feature, FeatureTestCase
    except Exception:
        return

    if (
        Feature._meta.db_table not in table_names
        or FeatureTestCase._meta.db_table not in table_names
    ):
        return

    feature_manager = Feature.objects.using(DEFAULT_DB_ALIAS)
    feature_test_manager = FeatureTestCase.objects.using(DEFAULT_DB_ALIAS)

    for result in results:
        for payload in result.features or []:
            slug = (payload.get("slug") or "").strip()
            package = (payload.get("package") or "").strip()
            if not slug:
                continue
            queryset = feature_manager.filter(slug=slug)
            if package:
                queryset = queryset.filter(package__name__iexact=package)
            for feature in queryset:
                feature_test_manager.update_or_create(
                    feature=feature,
                    test_node_id=result.node_id,
                    defaults={
                        "test_name": result.name,
                        "last_status": result.status,
                        "last_duration": result.duration,
                        "last_log": result.log,
                    },
                )
