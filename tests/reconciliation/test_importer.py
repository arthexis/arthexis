from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.energy.models import CustomerAccount, EnergyTariff, LedgerEntry
from apps.ocpp.models import Charger, Connector, MeterValue, OcppTransaction
from arthexis.reconciliation.importer import reconcile, write_receipt


class ReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.source = Path(self.temporary_directory.name) / "legacy.sqlite3"
        with sqlite3.connect(self.source) as database:
            database.executescript(
                """
                CREATE TABLE core_rfid (id integer, rfid text, custom_label text, active integer);
                CREATE TABLE cards_rfidattempt (id integer, rfid text, status text);
                CREATE TABLE core_energytariff (id integer, price_mxn numeric);
                CREATE TABLE core_account (id integer, name text, balance_kw numeric, energy_tariff_id integer);
                CREATE TABLE core_energytransaction (id integer, account_id integer, delta_kw numeric, charged_amount_mxn numeric, source text);
                CREATE TABLE core_sigilroot (id integer, prefix text, context_type text, active integer);
                CREATE TABLE core_stationmodel (id integer, vendor text, model text, preferred_ocpp_version text);
                CREATE TABLE ocpp_charger (id integer, charger_id text, station_model_id integer, active integer);
                CREATE TABLE ocpp_connector (id integer, charger_id integer, connector_id integer, status text);
                CREATE TABLE ocpp_transaction (id integer, charger_id integer, connector_id integer, account_id integer, transaction_id text, id_tag text, started_at text, meter_start numeric, meter_stop numeric);
                CREATE TABLE ocpp_metervalue (id integer, transaction_id integer, timestamp text, value numeric, measurand text, unit text, multiplier integer);
                CREATE TABLE ocpp_chargingprofile (id integer, charger_id integer, charging_profile_id text, stack_level integer, purpose text, kind text);
                CREATE TABLE ocpp_cpreservation (id integer, charger_id integer, connector_id integer, reservation_id text, id_tag text, expiry_date text, status text);
                CREATE TABLE ocpp_chargervariable (id integer, charger_id integer, component text, variable text, attribute_type text, value text);
                INSERT INTO core_rfid VALUES (1, 'raw-card-id-must-not-persist', 'Front desk', 1);
                INSERT INTO cards_rfidattempt VALUES (1, 'raw-card-id-must-not-persist', 'accepted');
                INSERT INTO core_energytariff VALUES (1, 2.75);
                INSERT INTO core_account VALUES (1, 'Legacy customer', 12.5, 1);
                INSERT INTO core_energytransaction VALUES (1, 1, 4.25, 100, 'manual_adjustment');
                INSERT INTO core_sigilroot VALUES (1, 'site', 'nodes.Node', 1);
                INSERT INTO core_stationmodel VALUES (1, 'Vendor', 'Model', 'ocpp1.6');
                INSERT INTO ocpp_charger VALUES (1, 'charger-legacy', 1, 1);
                INSERT INTO ocpp_connector VALUES (1, 1, 1, 'Available');
                INSERT INTO ocpp_transaction VALUES (1, 1, 1, 1, 'tx-1', 'TAG-1', '2026-01-01T00:00:00+00:00', 1, 2);
                INSERT INTO ocpp_metervalue VALUES (1, 1, '2026-01-01T00:01:00+00:00', 2, 'Energy.Active.Import.Register', 'Wh', 0);
                INSERT INTO ocpp_chargingprofile VALUES (1, 1, 'profile-1', 0, 'TxProfile', 'Absolute');
                INSERT INTO ocpp_cpreservation VALUES (1, 1, 1, 'reservation-1', 'TAG-1', '2026-01-02T00:00:00+00:00', 'pending');
                INSERT INTO ocpp_chargervariable VALUES (1, 1, 'EVSE', 'AvailabilityState', 'Actual', 'Available');
                """
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_dry_run_rolls_back_and_import_is_idempotent(self) -> None:
        dry_run = reconcile(self.source, dry_run=True)
        self.assertTrue(dry_run.dry_run)
        self.assertEqual(CardCredential.objects.count(), 0)

        report = reconcile(self.source)
        reconcile(self.source)

        self.assertEqual(CardCredential.objects.count(), 1)
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)
        self.assertEqual(EnergyTariff.objects.count(), 1)
        self.assertEqual(CustomerAccount.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(Charger.objects.count(), 1)
        self.assertEqual(Connector.objects.count(), 1)
        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(MeterValue.objects.count(), 1)
        self.assertEqual(report.imported["chargers"], 1)
        self.assertNotIn(
            "raw-card-id-must-not-persist", str(CardCredential.objects.values())
        )

    @override_settings(ARTHEXIS_DATA_DIR="/tmp")
    def test_receipt_is_redacted(self) -> None:
        report = reconcile(self.source, dry_run=True)
        receipt = write_receipt(report, Path(self.temporary_directory.name))

        data = json.loads(receipt.read_text())
        self.assertEqual(data["format"], "arthexis-reconciliation-v1")
        self.assertNotIn("raw-card-id-must-not-persist", receipt.read_text())

    def test_unrelated_sqlite_file_is_rejected_before_target_writes(self) -> None:
        unrelated = Path(self.temporary_directory.name) / "unrelated.sqlite3"
        with sqlite3.connect(unrelated) as database:
            database.execute("CREATE TABLE unrelated (id integer)")

        with self.assertRaisesMessage(ValueError, "supported Arthexis 1.x tables"):
            reconcile(unrelated)
        self.assertEqual(CardCredential.objects.count(), 0)
