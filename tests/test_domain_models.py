from datetime import UTC, datetime, timezone
from decimal import Decimal

from django.test import TestCase

from apps.cards.models import CardCredential
from apps.energy.models import CustomerAccount, EnergyTariff, LedgerEntry
from apps.events.models import EventEnvelope
from apps.nodes.models import Node, NodeLink, NodeRole
from apps.ocpp.models import (
    Charger,
    Connector,
    MeterValue,
    OcppTransaction,
    StationModel,
)
from apps.sigils.models import SigilRoot


class DomainModelTests(TestCase):
    def test_node_roles_and_topology_are_persisted(self) -> None:
        control = Node.objects.create(
            identifier="control-1", display_name="Control", role=NodeRole.CONTROL
        )
        terminal = Node.objects.create(
            identifier="terminal-1", display_name="Terminal", role=NodeRole.TERMINAL
        )
        link = NodeLink.objects.create(source=control, target=terminal)

        self.assertEqual(link.source.role, NodeRole.CONTROL)
        self.assertEqual(control.links.get(), link)

    def test_retained_charging_records_keep_cross_domain_links(self) -> None:
        tariff = EnergyTariff.objects.create(
            code="standard", price_mxn_per_kwh=Decimal("4.25")
        )
        account = CustomerAccount.objects.create(
            key="account-1", name="Account", tariff=tariff, ocpp_id_tag="tag-1"
        )
        card = CardCredential.objects.create(
            external_id="card-1", account=account, ocpp_id_tag="tag-1"
        )
        ledger = LedgerEntry.objects.create(
            account=account, delta_kwh=Decimal("10"), source="purchase"
        )
        station_model = StationModel.objects.create(vendor="ACME", model="Wallbox")
        charger = Charger.objects.create(
            identity="charger-1", station_model=station_model
        )
        connector = Connector.objects.create(charger=charger, number=1)
        transaction = OcppTransaction.objects.create(
            charger=charger,
            connector=connector,
            account=account,
            remote_id="transaction-1",
            id_tag=card.ocpp_id_tag,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        meter = MeterValue.objects.create(
            transaction=transaction,
            sampled_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            value=Decimal("1.5"),
        )
        event = EventEnvelope.objects.create(
            event_type="ocpp.authorization", producer="ocpp", payload={"accepted": True}
        )
        root = SigilRoot.objects.create(
            prefix="account", context_type="energy.CustomerAccount"
        )

        self.assertEqual(account.ledger_entries.get(), ledger)
        self.assertEqual(transaction.meter_values.get(), meter)
        self.assertEqual(event.payload["accepted"], True)
        self.assertEqual(root.context_type, "energy.CustomerAccount")
