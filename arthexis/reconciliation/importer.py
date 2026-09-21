"""Idempotent transformations from selected 1.x tables into 2.0 models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils.text import slugify
from django.utils.timezone import now

from arthexis.reconciliation.source import LegacySource, inspect_source


def _first(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    return next(
        (row[name] for name in names if row.get(name) not in (None, "")), default
    )


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _stable(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(f"{prefix}:{value}".encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _key(prefix: str, value: Any) -> str:
    return (slugify(str(value)) or prefix)[:70] + f"-{value}"


def _bool(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.lower() not in {"0", "false", "no", "off"}
    return bool(value)


@dataclass
class ReconciliationReport:
    """Redacted reconciliation outcome suitable for an operator receipt."""

    source: str
    source_sha256: str
    source_size: int
    dry_run: bool
    imported: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    def count(self, name: str, amount: int = 1) -> None:
        self.imported[name] = self.imported.get(name, 0) + amount

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "arthexis-reconciliation-v1",
            "source": self.source,
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
            "dry_run": self.dry_run,
            "imported": self.imported,
            "skipped": self.skipped,
        }


class _Importer:
    def __init__(self, source: LegacySource, report: ReconciliationReport) -> None:
        self.source, self.report = source, report
        self.accounts: dict[Any, object] = {}
        self.cards_by_value: dict[str, object] = {}
        self.chargers: dict[Any, object] = {}
        self.connectors: dict[tuple[Any, Any], object] = {}
        self.transactions: dict[Any, object] = {}

    def _rows(self, label: str, *tables: str) -> list[dict[str, Any]]:
        found, rows = self.source.rows(*tables)
        if found is None:
            self.report.skipped[label] = "source table absent"
        return rows

    def cards(self) -> None:
        from apps.cards.models import AuthorizationAttempt, CardCredential

        for row in self._rows("card_credentials", "core_rfid"):
            raw = _first(row, "rfid", "uid", default="")
            if not raw:
                continue
            card, _ = CardCredential.objects.update_or_create(
                external_id=_stable("legacy-card", raw),
                defaults={
                    "label": str(_first(row, "custom_label", "generated_label"))[:120],
                    "ocpp_id_tag": str(_first(row, "ocpp_id_tag", default=""))[:20],
                    "active": _bool(_first(row, "active", default=True)),
                },
            )
            self.cards_by_value[str(raw)] = card
            self.report.count("card_credentials")
        for row in self._rows("authorization_attempts", "cards_rfidattempt"):
            raw = _first(row, "rfid", "presented_id", default="")
            if not raw:
                continue
            accepted = str(_first(row, "status", default="")).lower() == "accepted"
            AuthorizationAttempt.objects.get_or_create(
                presented_id=_stable("legacy-presentation", raw),
                reason=str(_first(row, "reason", "status", default="legacy"))[:240],
                defaults={
                    "card": self.cards_by_value.get(str(raw)),
                    "accepted": accepted,
                },
            )
            self.report.count("authorization_attempts")

    def tariffs_and_accounts(self) -> None:
        from apps.energy.models import CustomerAccount, EnergyTariff

        tariffs: dict[Any, object] = {}
        for row in self._rows("energy_tariffs", "core_energytariff"):
            legacy_id = _first(row, "id", "pk")
            tariff, _ = EnergyTariff.objects.update_or_create(
                code=_key("tariff", legacy_id),
                defaults={
                    "price_mxn_per_kwh": _decimal(_first(row, "price_mxn", "price"))
                },
            )
            tariffs[legacy_id] = tariff
            self.report.count("energy_tariffs")
        for row in self._rows("customer_accounts", "core_account"):
            legacy_id = _first(row, "id", "pk")
            name = str(_first(row, "name", default=f"Legacy account {legacy_id}"))[:160]
            account, _ = CustomerAccount.objects.update_or_create(
                key=_key("account", legacy_id),
                defaults={
                    "name": name,
                    "ocpp_id_tag": str(_first(row, "ocpp_id_tag", default=""))[:20],
                    "tariff": tariffs.get(_first(row, "energy_tariff_id", "tariff_id")),
                    "balance_kwh": _decimal(_first(row, "balance_kw", "balance_kwh")),
                    "active": _bool(_first(row, "active", default=True)),
                },
            )
            self.accounts[legacy_id] = account
            self.report.count("customer_accounts")

    def ledger(self) -> None:
        from apps.energy.models import LedgerEntry

        for row in self._rows("ledger_entries", "core_energytransaction"):
            legacy_id = _first(row, "id", "pk")
            account = self.accounts.get(_first(row, "account_id"))
            if account is None:
                self.report.skipped["ledger_entries"] = "account dependency absent"
                continue
            LedgerEntry.objects.update_or_create(
                account=account,
                external_reference=_stable("legacy-ledger", legacy_id),
                defaults={
                    "delta_kwh": _decimal(_first(row, "delta_kw", "delta_kwh")),
                    "amount_mxn": _decimal(
                        _first(row, "charged_amount_mxn", "amount_mxn")
                    ),
                    "source": str(_first(row, "source", default="legacy"))[:40],
                },
            )
            self.report.count("ledger_entries")

    def topology(self) -> None:
        from apps.nodes.models import Node, NodeLink, NodeRole
        from apps.sigils.models import SigilRoot

        nodes: dict[Any, object] = {}
        for row in self._rows("nodes", "nodes_node", "core_node"):
            legacy_id = _first(row, "id", "pk")
            role = str(_first(row, "role", default=NodeRole.SATELLITE))
            if role not in NodeRole.values:
                role = NodeRole.SATELLITE
            node, _ = Node.objects.update_or_create(
                identifier=_key(
                    "node", _first(row, "identifier", "name", default=legacy_id)
                ),
                defaults={
                    "display_name": str(
                        _first(row, "name", "identifier", default=legacy_id)
                    )[:120],
                    "role": role,
                },
            )
            nodes[legacy_id] = node
            self.report.count("nodes")
        for row in self._rows("node_links", "nodes_nodelink", "core_nodelink"):
            source = nodes.get(_first(row, "source_id"))
            target = nodes.get(_first(row, "target_id"))
            if source and target:
                NodeLink.objects.update_or_create(
                    source=source,
                    target=target,
                    defaults={
                        "relation": str(_first(row, "relation", default="peer"))[:40]
                    },
                )
                self.report.count("node_links")
        for row in self._rows("sigil_roots", "core_sigilroot"):
            prefix = str(_first(row, "prefix", "name", default=""))[:80]
            if prefix:
                SigilRoot.objects.update_or_create(
                    prefix=prefix,
                    defaults={
                        "context_type": str(
                            _first(row, "context_type", "context", default="legacy")
                        )[:120],
                        "active": _bool(_first(row, "active", default=True)),
                    },
                )
                self.report.count("sigil_roots")

    def ocpp_assets(self) -> None:
        from apps.ocpp.models import Charger, Connector, StationModel

        models: dict[Any, object] = {}
        for row in self._rows("station_models", "core_stationmodel"):
            legacy_id = _first(row, "id", "pk")
            station, _ = StationModel.objects.update_or_create(
                vendor=str(_first(row, "vendor", "manufacturer", default="Legacy"))[
                    :120
                ],
                model=str(_first(row, "model", "name", default=legacy_id))[:120],
                defaults={
                    "family": str(_first(row, "family", default=""))[:120],
                    "preferred_protocol": str(
                        _first(
                            row,
                            "preferred_ocpp_version",
                            "preferred_protocol",
                            default="ocpp1.6",
                        )
                    )[:20],
                },
            )
            models[legacy_id] = station
            self.report.count("station_models")
        for row in self._rows("chargers", "ocpp_charger"):
            legacy_id = _first(row, "id", "pk")
            identity = str(_first(row, "charger_id", "identity", default=legacy_id))[
                :120
            ]
            charger, _ = Charger.objects.update_or_create(
                identity=identity,
                defaults={
                    "station_model": models.get(_first(row, "station_model_id")),
                    "active": _bool(_first(row, "active", default=True)),
                },
            )
            self.chargers[legacy_id] = charger
            self.report.count("chargers")
        for row in self._rows("connectors", "ocpp_connector"):
            charger_id = _first(row, "charger_id", "charge_point_id")
            number = _first(row, "number", "connector_id", default=0)
            charger = self.chargers.get(charger_id)
            if charger is not None and int(number) > 0:
                connector, _ = Connector.objects.update_or_create(
                    charger=charger,
                    number=int(number),
                    defaults={
                        "status": str(
                            _first(row, "status", "last_status", default="available")
                        )[:30]
                    },
                )
                self.connectors[(charger_id, number)] = connector
                self.report.count("connectors")

    def sessions(self) -> None:
        from apps.ocpp.models import MeterValue, OcppTransaction

        for row in self._rows("ocpp_transactions", "ocpp_transaction"):
            legacy_id = _first(row, "id", "pk")
            charger_id = _first(row, "charger_id", "charge_point_id")
            charger = self.chargers.get(charger_id)
            if charger is None:
                self.report.skipped["ocpp_transactions"] = "charger dependency absent"
                continue
            remote_id = str(
                _first(row, "transaction_id", "remote_id", default=legacy_id)
            )[:80]
            target, _ = OcppTransaction.objects.update_or_create(
                remote_id=remote_id,
                defaults={
                    "charger": charger,
                    "connector": self.connectors.get(
                        (charger_id, _first(row, "connector_id"))
                    ),
                    "account": self.accounts.get(_first(row, "account_id")),
                    "id_tag": str(_first(row, "id_tag", "idTag", default=""))[:20],
                    "started_at": _first(
                        row, "started_at", "start_time", default=now()
                    ),
                    "stopped_at": _first(row, "stopped_at", "stop_time", default=None),
                    "meter_start": _decimal(_first(row, "meter_start", default=0)),
                    "meter_stop": _decimal(_first(row, "meter_stop", default=0)),
                },
            )
            self.transactions[legacy_id] = target
            self.report.count("ocpp_transactions")
        for row in self._rows("meter_values", "ocpp_metervalue"):
            target = self.transactions.get(_first(row, "transaction_id"))
            if target is not None:
                MeterValue.objects.get_or_create(
                    transaction=target,
                    sampled_at=_first(row, "timestamp", "sampled_at", default=now()),
                    measurand=str(
                        _first(
                            row, "measurand", default="Energy.Active.Import.Register"
                        )
                    )[:60],
                    defaults={
                        "value": _decimal(_first(row, "value", default=0)),
                        "unit": str(_first(row, "unit", default="Wh"))[:12],
                        "multiplier": int(_first(row, "multiplier", default=0)),
                    },
                )
                self.report.count("meter_values")

    def run(self) -> None:
        self.cards()
        self.tariffs_and_accounts()
        self.ledger()
        self.topology()
        self.ocpp_assets()
        self.sessions()
        from arthexis.reconciliation.ocpp import import_ocpp_records

        import_ocpp_records(self)


def reconcile(source_path: Path, *, dry_run: bool = False) -> ReconciliationReport:
    """Reconcile supported 1.x logical records without changing the source."""
    inspection = inspect_source(source_path)
    if inspection.classification != "legacy":
        raise ValueError(
            "Reconciliation requires a legacy Arthexis database; "
            f"detected {inspection.classification}."
        )
    report = ReconciliationReport(
        source=source_path.name,
        source_sha256=inspection.sha256,
        source_size=inspection.size,
        dry_run=dry_run,
    )
    with LegacySource(source_path) as source:
        source.validate()
        importer = _Importer(source, report)
        if dry_run:
            with transaction.atomic():
                importer.run()
                transaction.set_rollback(True)
        else:
            with transaction.atomic():
                importer.run()
    return report


def write_receipt(report: ReconciliationReport, data_dir: Path) -> Path:
    """Write a redacted receipt; no source row values are included."""
    destination = data_dir / "reconciliation"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "latest.json"
    path.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n")
    return path
