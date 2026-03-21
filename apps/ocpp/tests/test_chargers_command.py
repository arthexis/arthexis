"""Tests for the ``chargers`` management command verbs and legacy aliases."""

import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.cards.models import RFID
from apps.ocpp import store
from apps.ocpp.management.commands.chargers import Command as ChargersCommand
from apps.ocpp.models import Charger


class ChargersCommandTests(TestCase):
    """Validate charger command verbs and backward-compatible legacy flags."""

    def test_sets_ws_auth_user_with_password(self) -> None:
        """Setting websocket auth binds the matched charger and persists credentials."""

        charger = Charger.objects.create(charger_id="CLI-WS-1")

        call_command(
            "charger",
            "auth",
            "set",
            "cp-user",
            "secret123",
            "--sn",
            charger.charger_id,
        )

        charger.refresh_from_db()
        user = get_user_model().objects.get(username="cp-user")
        self.assertEqual(charger.ws_auth_user_id, user.pk)
        self.assertIsNone(charger.ws_auth_group_id)
        self.assertTrue(user.check_password("secret123"))

    def test_clears_ws_auth_protection(self) -> None:
        """Clearing websocket auth removes both user and group protection fields."""

        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="bound-user", password="startpass"
        )
        charger = Charger.objects.create(charger_id="CLI-WS-2", ws_auth_user=user)

        call_command("charger", "auth", "clear", "--sn", charger.charger_id)

        charger.refresh_from_db()
        self.assertIsNone(charger.ws_auth_user_id)
        self.assertIsNone(charger.ws_auth_group_id)

    def test_requires_password_when_username_is_provided(self) -> None:
        """Username-based websocket auth requires an explicit password option."""

        Charger.objects.create(charger_id="CLI-WS-3")

        with self.assertRaisesMessage(CommandError, "--ws-auth-password is required."):
            call_command(
                "chargers", "--sn", "CLI-WS-3", "--ws-auth-username", "cp-user"
            )

    def test_requires_username_when_ws_auth_username_is_blank(self) -> None:
        """Whitespace-only websocket usernames are rejected with the right error."""

        Charger.objects.create(charger_id="CLI-WS-3B")

        with self.assertRaisesMessage(CommandError, "--ws-auth-username is required."):
            call_command(
                "chargers",
                "--sn",
                "CLI-WS-3B",
                "--ws-auth-username",
                "   ",
                "--ws-auth-password",
                "secret123",
            )

    def test_requires_effective_cp_selector_for_ws_auth_changes(self) -> None:
        """Whitespace-only ``--cp`` values do not bypass selector validation."""

        Charger.objects.create(charger_id="CLI-WS-4")

        with self.assertRaisesMessage(CommandError, "Websocket auth changes require"):
            call_command(
                "chargers",
                "--cp",
                "   ",
                "--ws-auth-clear",
            )

    def test_reactivates_existing_inactive_ws_auth_user(self) -> None:
        """Updating existing websocket auth credentials reactivates the user."""

        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="inactive-user",
            password="oldpass",
            is_active=False,
        )
        charger = Charger.objects.create(charger_id="CLI-WS-5")

        call_command(
            "chargers",
            "--sn",
            charger.charger_id,
            "--ws-auth-username",
            user.username,
            "--ws-auth-password",
            "newpass123",
        )

        user.refresh_from_db()
        charger.refresh_from_db()
        self.assertEqual(charger.ws_auth_user_id, user.pk)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("newpass123"))

    def test_rename_base_charger_renames_connectors_automatically(self) -> None:
        """Renaming a base charger updates connector names with letter suffixes."""

        Charger.objects.create(
            charger_id="CLI-REN-1", connector_id=None, display_name="Old"
        )
        connector_a = Charger.objects.create(
            charger_id="CLI-REN-1", connector_id=1, display_name="Old A"
        )
        connector_b = Charger.objects.create(
            charger_id="CLI-REN-1", connector_id=2, display_name="Old B"
        )

        call_command("charger", "rename", "Main Hub", "--sn", "CLI-REN-1")

        connector_a.refresh_from_db()
        connector_b.refresh_from_db()
        self.assertEqual(connector_a.display_name, "Main Hub A")
        self.assertEqual(connector_b.display_name, "Main Hub B")

    def test_send_restart_registers_pending_call(self) -> None:
        """Restart requests send Reset and register timeout-tracked pending metadata."""

        charger = Charger.objects.create(charger_id="CLI-RST-1", connector_id=1)

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
            call_command("charger", "restart", "--sn", "CLI-RST-1", "--cp", "A")

        self.assertEqual(len(ws.messages), 1)
        frame = json.loads(ws.messages[0])
        self.assertEqual(frame[2], "Reset")

        metadata = store.pop_pending_call(frame[1])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.get("action"), "Reset")

    def test_send_stop_for_station_targets_each_active_connector(self) -> None:
        """Remote stop keeps multi-connector selections and dispatches each active session."""

        Charger.objects.create(charger_id="CLI-STOP-1", connector_id=1)
        Charger.objects.create(charger_id="CLI-STOP-1", connector_id=2)

        class DummyWs:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def send(self, payload: str) -> None:
                self.messages.append(payload)

        class DummyTx:
            def __init__(self, pk: int) -> None:
                self.pk = pk

        ws_a = DummyWs()
        ws_b = DummyWs()

        def fake_get_connection(charger_id: str, connector_id: int | None):
            return ws_a if connector_id == 1 else ws_b if connector_id == 2 else None

        def fake_get_transaction(charger_id: str, connector_id: int | None):
            if connector_id == 1:
                return DummyTx(101)
            if connector_id == 2:
                return DummyTx(202)
            return None

        with (
            patch(
                "apps.ocpp.management.commands.chargers.store.get_connection",
                side_effect=fake_get_connection,
            ),
            patch(
                "apps.ocpp.management.commands.chargers.store.get_transaction",
                side_effect=fake_get_transaction,
            ),
            patch("apps.ocpp.management.commands.chargers.store.schedule_call_timeout"),
        ):
            call_command("charger", "stop", "--sn", "CLI-STOP-1")

        frame_a = json.loads(ws_a.messages[0])
        frame_b = json.loads(ws_b.messages[0])
        self.assertEqual(frame_a[2], "RemoteStopTransaction")
        self.assertEqual(frame_b[2], "RemoteStopTransaction")
        self.assertEqual(frame_a[3]["transactionId"], 101)
        self.assertEqual(frame_b[3]["transactionId"], 202)
        self.assertIsNotNone(store.pop_pending_call(frame_a[1]))
        self.assertIsNotNone(store.pop_pending_call(frame_b[1]))

    def test_send_stop_skips_chargers_without_active_transaction(self) -> None:
        """Remote stop continues processing when one selected charger has no active session."""

        Charger.objects.create(charger_id="CLI-STOP-2", connector_id=1)
        Charger.objects.create(charger_id="CLI-STOP-2", connector_id=2)

        class DummyWs:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def send(self, payload: str) -> None:
                self.messages.append(payload)

        class DummyTx:
            def __init__(self, pk: int) -> None:
                self.pk = pk

        ws_a = DummyWs()

        def fake_get_connection(charger_id: str, connector_id: int | None):
            return ws_a if connector_id == 1 else None

        def fake_get_transaction(charger_id: str, connector_id: int | None):
            return DummyTx(303) if connector_id == 1 else None

        with (
            patch(
                "apps.ocpp.management.commands.chargers.store.get_connection",
                side_effect=fake_get_connection,
            ),
            patch(
                "apps.ocpp.management.commands.chargers.store.get_transaction",
                side_effect=fake_get_transaction,
            ),
            patch("apps.ocpp.management.commands.chargers.store.schedule_call_timeout"),
        ):
            call_command("charger", "stop", "--sn", "CLI-STOP-2")

        self.assertEqual(len(ws_a.messages), 1)
        frame = json.loads(ws_a.messages[0])
        self.assertEqual(frame[2], "RemoteStopTransaction")
        self.assertEqual(frame[3]["transactionId"], 303)
        self.assertIsNotNone(store.pop_pending_call(frame[1]))

    def test_restart_for_cp_all_targets_single_base_charger(self) -> None:
        """Restart collapses connector-only station selections to one base reset call."""

        Charger.objects.create(charger_id="CLI-RST-ALL-1", connector_id=None)
        Charger.objects.create(charger_id="CLI-RST-ALL-1", connector_id=1)
        Charger.objects.create(charger_id="CLI-RST-ALL-1", connector_id=2)

        class DummyWs:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def send(self, payload: str) -> None:
                self.messages.append(payload)

        ws_base = DummyWs()

        with (
            patch(
                "apps.ocpp.management.commands.chargers.store.get_connection",
                return_value=ws_base,
            ),
            patch("apps.ocpp.management.commands.chargers.store.schedule_call_timeout"),
        ):
            call_command(
                "charger", "restart", "--sn", "CLI-RST-ALL-1", "--cp", "all"
            )

        self.assertEqual(len(ws_base.messages), 1)
        frame = json.loads(ws_base.messages[0])
        self.assertEqual(frame[2], "Reset")
        self.assertIsNotNone(store.pop_pending_call(frame[1]))

    def test_rename_requires_tty_when_value_not_provided(self) -> None:
        """Valueless rename fails fast outside interactive terminals."""

        charger = Charger.objects.create(
            charger_id="CLI-REN-NONTTY-1", connector_id=None
        )
        command = ChargersCommand()
        command.stdin = io.StringIO()

        with self.assertRaisesMessage(CommandError, "interactive terminal"):
            command._rename_charger(charger, "", interactive=True)

    def test_send_restart_reports_transport_error_as_command_error(self) -> None:
        """Restart send failures are surfaced as controlled command errors."""

        Charger.objects.create(charger_id="CLI-RST-ERR-1", connector_id=1)

        class DummyWs:
            async def send(self, payload: str) -> None:
                raise RuntimeError("socket down")

        with patch(
            "apps.ocpp.management.commands.chargers.store.get_connection",
            return_value=DummyWs(),
        ):
            with self.assertRaisesMessage(CommandError, "failed to send Reset"):
                call_command(
                    "charger", "restart", "--sn", "CLI-RST-ERR-1", "--cp", "A"
                )

    def test_charger_alias_defaults_to_base_charger(self) -> None:
        """The ``charger`` alias selects the default base charger without selectors."""

        base = Charger.objects.create(charger_id="CLI-ALIAS-1", connector_id=None)
        Charger.objects.create(charger_id="CLI-ALIAS-1", connector_id=1)

        call_command("charger", "--rename", "Alias Name")

        base.refresh_from_db()
        self.assertEqual(base.display_name, "Alias Name")

    def test_send_local_rfids_sends_sendlocallist(self) -> None:
        """Sending local RFIDs dispatches a full ``SendLocalList`` with released cards."""

        RFID.objects.create(rfid="A1B2C3D4", released=True)
        RFID.objects.create(rfid="DEADBEEF", released=False)
        charger = Charger.objects.create(charger_id="CLI-RFID-LIST-1", connector_id=1)

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
            call_command(
                "charger",
                "rfid",
                "push",
                "--sn",
                charger.charger_id,
                "--cp",
                "A",
            )

        self.assertEqual(len(ws.messages), 1)
        frame = json.loads(ws.messages[0])
        self.assertEqual(frame[2], "SendLocalList")
        self.assertEqual(frame[3]["updateType"], "Full")
        self.assertEqual(frame[3]["listVersion"], 1)
        self.assertEqual(
            frame[3]["localAuthorizationList"],
            [{"idTag": "A1B2C3D4", "idTagInfo": {"status": "Accepted"}}],
        )

    def test_rfid_lockdown_enables_requirement_and_sends_local_list(self) -> None:
        """RFID lockdown toggles requirement on and pushes the released list."""

        RFID.objects.create(rfid="AB12CD34", released=True)
        charger = Charger.objects.create(
            charger_id="CLI-RFID-LOCK-1", connector_id=1, require_rfid=False
        )

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
            call_command(
                "charger",
                "rfid",
                "lock",
                "--sn",
                charger.charger_id,
                "--cp",
                "A",
            )

        charger.refresh_from_db()
        self.assertTrue(charger.require_rfid)
        self.assertEqual(len(ws.messages), 1)
        frame = json.loads(ws.messages[0])
        self.assertEqual(frame[2], "SendLocalList")

    def test_rfid_lockdown_cannot_be_combined_with_send_local_rfids(self) -> None:
        """Lockdown rejects duplicate list-send intent on the same command call."""

        Charger.objects.create(charger_id="CLI-RFID-LOCK-2", connector_id=1)

        with self.assertRaisesMessage(CommandError, "already sends local RFIDs"):
            call_command(
                "chargers",
                "--sn",
                "CLI-RFID-LOCK-2",
                "--cp",
                "A",
                "--rfid-lockdown",
                "--send-local-rfids",
            )
