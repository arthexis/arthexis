import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.nodes.models import Node
from apps.ocpp import store
from apps.ocpp.models import Charger


class ChargerRestartGuardTests(TestCase):
    def _create_local_and_downstream_nodes(self) -> tuple[Node, Node]:
        Node._local_cache.clear()
        local = Node.objects.create(
            hostname="local-node",
            mac_address=Node.get_current_mac(),
            current_relation=Node.Relation.SELF,
            public_endpoint="local-node",
        )
        downstream = Node.objects.create(
            hostname="downstream-node",
            current_relation=Node.Relation.DOWNSTREAM,
            public_endpoint="downstream-node",
        )
        return local, downstream

    def test_restart_allows_legacy_local_origin_charger(self) -> None:
        Charger.objects.create(charger_id="CLI-RST-LOCAL-1", connector_id=1)

        class DummyWs:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def send(self, payload: str) -> None:
                self.messages.append(payload)

        ws = DummyWs()

        with (
            patch(
                "apps.ocpp.management.commands.chargers.store.get_connection",
                return_value=ws,
            ),
            patch("apps.ocpp.management.commands.chargers.store.schedule_call_timeout"),
        ):
            call_command("charger", "restart", "--sn", "CLI-RST-LOCAL-1", "--cp", "A")

        self.assertEqual(len(ws.messages), 1)
        frame = json.loads(ws.messages[0])
        self.assertEqual(frame[2], "Reset")
        self.assertIsNotNone(store.pop_pending_call(frame[1]))

    def test_restart_refuses_downstream_charger_before_connection_lookup(self) -> None:
        _local, downstream = self._create_local_and_downstream_nodes()
        Charger.objects.create(
            charger_id="CLI-RST-DOWN-1",
            connector_id=None,
            node_origin=downstream,
        )

        with patch(
            "apps.ocpp.management.commands.chargers.store.get_connection"
        ) as get_connection:
            with self.assertRaisesMessage(
                CommandError,
                "Refusing to restart downstream charger CLI-RST-DOWN-1",
            ):
                call_command("charger", "restart", "--sn", "CLI-RST-DOWN-1")

        get_connection.assert_not_called()

    def test_legacy_send_restart_refuses_downstream_charger(self) -> None:
        _local, downstream = self._create_local_and_downstream_nodes()
        Charger.objects.create(
            charger_id="CLI-RST-DOWN-2",
            connector_id=None,
            node_origin=downstream,
        )

        with patch(
            "apps.ocpp.management.commands.chargers.store.get_connection"
        ) as get_connection:
            with self.assertRaisesMessage(
                CommandError,
                "Refusing to restart downstream charger CLI-RST-DOWN-2",
            ):
                call_command(
                    "chargers",
                    "--sn",
                    "CLI-RST-DOWN-2",
                    "--send-restart",
                )

        get_connection.assert_not_called()

    def test_restart_for_cp_all_refuses_downstream_base_charger(self) -> None:
        _local, downstream = self._create_local_and_downstream_nodes()
        Charger.objects.create(
            charger_id="CLI-RST-DOWN-ALL-1",
            connector_id=None,
            node_origin=downstream,
        )
        Charger.objects.create(
            charger_id="CLI-RST-DOWN-ALL-1",
            connector_id=1,
            node_origin=downstream,
        )
        Charger.objects.create(
            charger_id="CLI-RST-DOWN-ALL-1",
            connector_id=2,
            node_origin=downstream,
        )

        with patch(
            "apps.ocpp.management.commands.chargers.store.get_connection"
        ) as get_connection:
            with self.assertRaisesMessage(
                CommandError,
                "Refusing to restart downstream charger CLI-RST-DOWN-ALL-1",
            ):
                call_command(
                    "charger",
                    "restart",
                    "--sn",
                    "CLI-RST-DOWN-ALL-1",
                    "--cp",
                    "all",
                )

        get_connection.assert_not_called()
