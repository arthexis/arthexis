"""Verify a reconciled migration fixture and produce GO/NO-GO evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from arthexis.reconciliation.source import inspect_source
from arthexis.reconciliation.workspace import verify_fixture_source

REPORT_FORMAT = "arthexis-migration-report-v1"

RESOURCE_TABLES = {
    "card_credentials": "cards_cardcredential",
    "authorization_attempts": "cards_authorizationattempt",
    "energy_tariffs": "energy_energytariff",
    "customer_accounts": "energy_customeraccount",
    "ledger_entries": "energy_ledgerentry",
    "nodes": "nodes_node",
    "node_links": "nodes_nodelink",
    "sigil_roots": "sigils_sigilroot",
    "station_models": "ocpp_stationmodel",
    "chargers": "ocpp_charger",
    "connectors": "ocpp_connector",
    "ocpp_transactions": "ocpp_ocpptransaction",
    "meter_values": "ocpp_metervalue",
    "charging_profiles": "ocpp_chargingprofile",
    "reservations": "ocpp_reservation",
    "charger_variables": "ocpp_chargervariable",
    "certificate_metadata": "ocpp_certificaterecord",
    "notifications": "ocpp_notificationrecord",
    "monitoring": "ocpp_monitoringrecord",
    "operational_status": "ocpp_operationalstatusrecord",
}


@dataclass(frozen=True)
class VerificationResult:
    decision: str
    report: dict[str, object]
    json_path: Path
    text_path: Path


def _count_rows(database: Path, table: str) -> int:
    quoted = table.replace('"', '""')
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]


def _foreign_key_violations(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return connection.execute("PRAGMA foreign_key_check").fetchall()


def _render_text(report: dict[str, object]) -> str:
    lines = [
        "Arthexis migration verification report",
        f"Decision: {report['decision']}",
        f"Fixture: {report['fixture_id']}",
        f"Source capture: {report['source_capture_id']}",
        "",
        "Checks:",
    ]
    for check in report["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- [{status}] {check['name']}: {check['detail']}")
    if report["warnings"]:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    if report["failures"]:
        lines.extend(["", "Failures:"])
        lines.extend(f"- {failure}" for failure in report["failures"])
    lines.append("")
    return "\n".join(lines)


def verify_reconciliation(fixture_path: Path) -> VerificationResult:
    """Verify local reconciliation evidence without modifying either database."""

    fixture = fixture_path.expanduser().resolve()
    fixture_metadata, source_database = verify_fixture_source(fixture)

    reconciliation_path = fixture / "reconciliation.json"
    destination = fixture / "reconciled.sqlite3"
    if not reconciliation_path.is_file():
        raise ValueError(f"Reconciliation receipt is missing: {reconciliation_path}")
    if not destination.is_file():
        raise ValueError(f"Reconciled database is missing: {destination}")

    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    inspection = inspect_source(destination)

    checks: list[dict[str, object]] = []
    warnings: list[str] = []
    failures: list[str] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            failures.append(f"{name}: {detail}")

    check(
        "reconciliation completed",
        reconciliation.get("status") == "success",
        f"status={reconciliation.get('status')}",
    )
    check(
        "fixture source unchanged",
        reconciliation.get("source_database_sha256")
        == fixture_metadata["database"]["sha256_at_creation"],
        "fixture source hash matches restore evidence",
    )
    check(
        "destination generation",
        inspection.classification == "v2",
        f"classification={inspection.classification}",
    )
    check(
        "destination integrity",
        inspection.integrity == "ok",
        f"integrity={inspection.integrity}",
    )

    violations = _foreign_key_violations(destination)
    check(
        "foreign key integrity",
        not violations,
        "no foreign-key violations"
        if not violations
        else f"{len(violations)} foreign-key violation(s)",
    )

    imported = reconciliation.get("reconciliation", {}).get("imported", {})
    for resource, expected in sorted(imported.items()):
        table = RESOURCE_TABLES.get(resource)
        if table is None:
            warnings.append(
                f"No destination count verifier is registered for imported resource {resource}."
            )
            continue
        actual = _count_rows(destination, table)
        check(
            f"retained count {resource}",
            actual == expected,
            f"imported={expected}, destination={actual}",
        )

    skipped = reconciliation.get("reconciliation", {}).get("skipped", {})
    for resource, reason in sorted(skipped.items()):
        if reason == "source table absent":
            warnings.append(f"{resource}: source table absent; no history was manufactured.")
        else:
            failures.append(f"{resource}: reconciliation skipped retained data ({reason}).")

    decision = "GO" if not failures else "NO-GO"
    report = {
        "format": REPORT_FORMAT,
        "decision": decision,
        "fixture_id": fixture_metadata["fixture_id"],
        "source_capture_id": fixture_metadata["source_capture_id"],
        "source_database": str(source_database),
        "destination_database": str(destination),
        "checks": checks,
        "warnings": warnings,
        "failures": failures,
        "resource_usage": reconciliation.get("resource_usage", {}),
    }

    json_path = fixture / "migration-report.json"
    text_path = fixture / "migration-report.txt"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text_path.write_text(_render_text(report), encoding="utf-8")
    return VerificationResult(
        decision=decision,
        report=report,
        json_path=json_path,
        text_path=text_path,
    )
