from datetime import datetime, timezone
from decimal import Decimal

from django.test import TestCase

from apps.cards.models import CardCredential
from apps.energy.models import CustomerAccount, EnergyTariff, LedgerEntry
from apps.events.models import EventEnvelope
from apps.nodes.models import Node, NodeLink, NodeRole
from apps.ocpp.models import MeterValue
from apps.sigils.models import SigilRoot
from tests.ocpp.builders import charger, connector, station_model, transaction


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
        model = station_model(model="Wallbox")
        selected_charger = charger("charger-1", station=model)
        selected_connector = connector(selected_charger, number=1)
        selected_transaction = transaction(
            selected_charger,
            "transaction-1",
            connector=selected_connector,
            account=account,
            id_tag=card.ocpp_id_tag,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        meter = MeterValue.objects.create(
            transaction=selected_transaction,
            sampled_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
            value=Decimal("1.5"),
        )
        event = EventEnvelope.objects.create(
            event_type="ocpp.authorization", producer="ocpp", payload={"accepted": True}
        )
        root = SigilRoot.objects.create(
            prefix="account", context_type="energy.CustomerAccount"
        )

        self.assertEqual(account.ledger_entries.get(), ledger)
        self.assertEqual(selected_transaction.meter_values.get(), meter)
        self.assertEqual(event.payload["accepted"], True)
        self.assertEqual(root.context_type, "energy.CustomerAccount")
