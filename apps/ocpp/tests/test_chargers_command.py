"""Tests for the ``chargers`` management command verbs and legacy aliases."""

import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.ocpp import store
from apps.ocpp.management.commands.chargers import Command as ChargersCommand
from apps.ocpp.models import AutoStartAttempt, Charger


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

    def test_enable_autostart_configures_charger_and_creates_service_account(
        self,
    ) -> None:
        """Auto-start stores its idTag and tracks it through a non-RFID account."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-1")

        call_command(
            "chargers",
            "enable-autostart",
            "AUTO-START-001",
            "--sn",
            charger.charger_id,
        )

        charger.refresh_from_db()
        account = CustomerAccount.objects.get(ocpp_id_tag="AUTO-START-001")
        self.assertEqual(charger.auto_start_id_tag, "AUTO-START-001")
        self.assertTrue(account.service_account)
        self.assertFalse(account.rfids.exists())

    def test_disable_autostart_unsets_charger_but_keeps_account(self) -> None:
        """Disabling a charger must preserve prior session attribution data."""

        charger = Charger.objects.create(
            charger_id="CLI-AUTOSTART-2", auto_start_id_tag="AUTO-START-002"
        )
        account = CustomerAccount.objects.create(
            name="AUTO-START AUTO-START-002",
            ocpp_id_tag="AUTO-START-002",
            service_account=True,
        )

        call_command("chargers", "disable-autostart", "--sn", charger.charger_id)

        charger.refresh_from_db()
        self.assertEqual(charger.auto_start_id_tag, "")
        self.assertTrue(CustomerAccount.objects.filter(pk=account.pk).exists())

    def test_enable_autostart_rejects_existing_non_service_account(self) -> None:
        """A customer account idTag cannot be repurposed for unattended charging."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-3")
        CustomerAccount.objects.create(
            name="Customer tag", ocpp_id_tag="AUTO-START-003", service_account=False
        )

        with self.assertRaisesMessage(CommandError, "belongs to a non-service account"):
            call_command(
                "chargers",
                "enable-autostart",
                "AUTO-START-003",
                "--sn",
                charger.charger_id,
            )

        charger.refresh_from_db()
        self.assertEqual(charger.auto_start_id_tag, "")

    def test_enable_autostart_rechecks_a_racing_non_service_account(self) -> None:
        """A unique-conflict account returned after lookup cannot be repurposed."""

        raced_account = CustomerAccount(
            name="Concurrent customer tag",
            ocpp_id_tag="AUTO-START-RACE",
            service_account=False,
        )
        command = ChargersCommand()

        with patch.object(
            CustomerAccount.objects,
            "get_or_create",
            return_value=(raced_account, False),
        ):
            with self.assertRaisesMessage(
                CommandError, "belongs to a non-service account"
            ):
                command._get_or_create_autostart_account("AUTO-START-RACE")

    def test_enable_autostart_allows_an_opaque_ocpp_id_tag(self) -> None:
        """OCPP auto-start credentials need not use an RFID-shaped namespace."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-4")

        call_command(
            "chargers",
            "enable-autostart",
            "TALLER",
            "--sn",
            charger.charger_id,
        )

        charger.refresh_from_db()
        account = CustomerAccount.objects.get(ocpp_id_tag="TALLER")
        self.assertEqual(charger.auto_start_id_tag, "TALLER")
        self.assertTrue(account.service_account)
        self.assertFalse(account.rfids.exists())

    def test_enable_autostart_rejects_non_ascii_or_control_characters(self) -> None:
        """OCPP idTags must remain printable ASCII CiStrings."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-4A")

        for id_tag in ("TALLÉR", "TALLER\t1", "\tTALLER", "TALLER\n"):
            with self.subTest(id_tag=id_tag):
                with self.assertRaisesMessage(
                    CommandError, "printable ASCII characters"
                ):
                    call_command(
                        "chargers",
                        "enable-autostart",
                        id_tag,
                        "--sn",
                        charger.charger_id,
                    )

        charger.refresh_from_db()
        self.assertEqual(charger.auto_start_id_tag, "")

    def test_enable_autostart_rejects_a_matching_rfid(self) -> None:
        """The collision check protects against legacy invalid RFID records too."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-5")
        RFID.objects.bulk_create([RFID(rfid="AUTO-START-005")])

        with self.assertRaisesMessage(CommandError, "conflicts with an RFID"):
            call_command(
                "chargers",
                "enable-autostart",
                "AUTO-START-005",
                "--sn",
                charger.charger_id,
            )

        charger.refresh_from_db()
        self.assertEqual(charger.auto_start_id_tag, "")

    def test_enable_autostart_allows_prefix_only_rfid(self) -> None:
        """A distinct RFID sharing the match prefix is not a credential collision."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-5C")
        RFID.objects.bulk_create([RFID(rfid="AUTO-START-005")])

        call_command(
            "chargers",
            "enable-autostart",
            "AUTO-START-005B",
            "--sn",
            charger.charger_id,
        )

        charger.refresh_from_db()
        self.assertEqual(charger.auto_start_id_tag, "AUTO-START-005B")

    def test_enable_autostart_rejects_a_reverse_uid_rfid(self) -> None:
        """The exact collision check also protects the reverse UID representation."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-5D")
        id_tag = "AUTO-START-005D"
        RFID.objects.bulk_create([RFID(rfid=RFID.reverse_uid(id_tag))])

        with self.assertRaisesMessage(CommandError, "conflicts with an RFID"):
            call_command(
                "chargers",
                "enable-autostart",
                id_tag,
                "--sn",
                charger.charger_id,
            )

        charger.refresh_from_db()
        self.assertEqual(charger.auto_start_id_tag, "")

    def test_enable_autostart_rechecks_rfid_collision_for_service_accounts(
        self,
    ) -> None:
        """An existing service account cannot bypass a later RFID collision."""

        charger = Charger.objects.create(charger_id="CLI-AUTOSTART-5B")
        CustomerAccount.objects.create(
            name="AUTO-START AUTO-START-005B",
            ocpp_id_tag="AUTO-START-005B",
            service_account=True,
        )
        RFID.objects.bulk_create([RFID(rfid="AUTO-START-005B")])

        with self.assertRaisesMessage(CommandError, "conflicts with an RFID"):
            call_command(
                "chargers",
                "enable-autostart",
                "AUTO-START-005B",
                "--sn",
                charger.charger_id,
            )

        charger.refresh_from_db()
        self.assertEqual(charger.auto_start_id_tag, "")

    def test_autostart_configuration_clears_a_stale_reservation(self) -> None:
        """Enable and disable both release a prior plugged-in reservation."""

        charger = Charger.objects.create(
            charger_id="CLI-AUTOSTART-6",
            auto_start_id_tag="AUTO-START-006",
        )
        stale_attempt = AutoStartAttempt.objects.create(
            charger=charger,
            reservation_scope="connector:1",
            id_tag="AUTO-START-006",
            message_id="cli-old-auto-start",
            action="RemoteStartTransaction",
            expires_at=timezone.now() + timedelta(minutes=1),
        )

        call_command("chargers", "disable-autostart", "--sn", charger.charger_id)

        stale_attempt.refresh_from_db()
        self.assertEqual(stale_attempt.state, AutoStartAttempt.State.RELEASED)
        newer_attempt = AutoStartAttempt.objects.create(
            charger=charger,
            reservation_scope="connector:1",
            id_tag="AUTO-START-OLD",
            message_id="cli-new-auto-start",
            action="RemoteStartTransaction",
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        call_command(
            "chargers",
            "enable-autostart",
            "AUTO-START-006",
            "--sn",
            charger.charger_id,
        )

        newer_attempt.refresh_from_db()
        self.assertEqual(newer_attempt.state, AutoStartAttempt.State.RELEASED)

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

    def test_config_get_sends_getconfiguration_to_base_charge_point(self) -> None:
        """Configuration reads send OCPP GetConfiguration to the station connection."""

        Charger.objects.create(charger_id="CLI-CFG-GET-1", connector_id=None)
        connector = Charger.objects.create(charger_id="CLI-CFG-GET-1", connector_id=1)

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
                "config",
                "get",
                "HeartbeatInterval",
                "LocalAuthorizeOffline",
                "--sn",
                connector.charger_id,
                "--cp",
                "A",
            )

        self.assertEqual(len(ws.messages), 1)
        frame = json.loads(ws.messages[0])
        self.assertEqual(frame[2], "GetConfiguration")
        self.assertEqual(
            frame[3],
            {"key": ["HeartbeatInterval", "LocalAuthorizeOffline"]},
        )
        metadata = store.pop_pending_call(frame[1])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertIsNone(metadata.get("connector_id"))
        self.assertEqual(metadata.get("action"), "GetConfiguration")

    def test_config_set_sends_changeconfiguration(self) -> None:
        """Configuration writes send OCPP ChangeConfiguration payloads."""

        charger = Charger.objects.create(charger_id="CLI-CFG-SET-1", connector_id=1)

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
                "config",
                "set",
                "LocalAuthorizeOffline",
                "true",
                "--sn",
                charger.charger_id,
                "--cp",
                "A",
            )

        self.assertEqual(len(ws.messages), 1)
        frame = json.loads(ws.messages[0])
        self.assertEqual(frame[2], "ChangeConfiguration")
        self.assertEqual(
            frame[3],
            {"key": "LocalAuthorizeOffline", "value": "true"},
        )
        metadata = store.pop_pending_call(frame[1])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.get("action"), "ChangeConfiguration")

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
            call_command("charger", "restart", "--sn", "CLI-RST-ALL-1", "--cp", "all")

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
                call_command("charger", "restart", "--sn", "CLI-RST-ERR-1", "--cp", "A")

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

    def test_rfid_open_sets_open_policy_and_disables_requirement(self) -> None:
        """RFID open mode sets insecure compatibility auth policy per charger."""

        charger = Charger.objects.create(
            charger_id="CLI-RFID-OPEN-1",
            connector_id=1,
            authorization_policy=Charger.AuthorizationPolicy.STRICT,
            require_rfid=True,
        )

        call_command("charger", "rfid", "open", "--sn", charger.charger_id, "--cp", "A")

        charger.refresh_from_db()
        self.assertEqual(charger.authorization_policy, Charger.AuthorizationPolicy.OPEN)
        self.assertFalse(charger.require_rfid)

    def test_rfid_open_all_selects_every_configured_charger(self) -> None:
        """The explicit all selector enables field/debug open auth globally."""

        first = Charger.objects.create(
            charger_id="CLI-RFID-OPEN-ALL-1",
            connector_id=1,
            authorization_policy=Charger.AuthorizationPolicy.STRICT,
            require_rfid=True,
        )
        second = Charger.objects.create(
            charger_id="CLI-RFID-OPEN-ALL-2",
            connector_id=1,
            authorization_policy=Charger.AuthorizationPolicy.STRICT,
            require_rfid=True,
        )

        call_command("charger", "rfid", "open", "--all")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.authorization_policy, Charger.AuthorizationPolicy.OPEN)
        self.assertEqual(second.authorization_policy, Charger.AuthorizationPolicy.OPEN)
        self.assertFalse(first.require_rfid)
        self.assertFalse(second.require_rfid)

    def test_selector_before_verb_is_not_overwritten_by_subparser_defaults(self) -> None:
        """A root selector must still target the intended charger after the verb."""

        selected = Charger.objects.create(
            charger_id="CLI-RFID-ROOT-SELECTOR-1",
            authorization_policy=Charger.AuthorizationPolicy.STRICT,
            require_rfid=True,
        )
        other = Charger.objects.create(
            charger_id="CLI-RFID-ROOT-SELECTOR-2",
            authorization_policy=Charger.AuthorizationPolicy.STRICT,
            require_rfid=True,
        )

        call_command("charger", "--sn", selected.charger_id, "rfid", "open")

        selected.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(selected.authorization_policy, Charger.AuthorizationPolicy.OPEN)
        self.assertFalse(selected.require_rfid)
        self.assertEqual(other.authorization_policy, Charger.AuthorizationPolicy.STRICT)
        self.assertTrue(other.require_rfid)

    def test_all_selector_cannot_be_combined_with_specific_selector(self) -> None:
        """All-charger debug actions must not be mixed with narrower selectors."""

        Charger.objects.create(charger_id="CLI-RFID-OPEN-ALL-3", connector_id=1)

        with self.assertRaisesMessage(CommandError, "Use --all by itself"):
            call_command(
                "charger",
                "rfid",
                "open",
                "--all",
                "--sn",
                "CLI-RFID-OPEN-ALL-3",
            )

    def test_rfid_strict_sets_strict_policy_and_enables_requirement(self) -> None:
        """RFID strict mode leaves open mode and restores RFID requirement."""

        charger = Charger.objects.create(
            charger_id="CLI-RFID-STRICT-1",
            connector_id=1,
            authorization_policy=Charger.AuthorizationPolicy.OPEN,
            require_rfid=False,
        )

        call_command(
            "charger", "rfid", "strict", "--sn", charger.charger_id, "--cp", "A"
        )

        charger.refresh_from_db()
        self.assertEqual(
            charger.authorization_policy, Charger.AuthorizationPolicy.STRICT
        )
        self.assertTrue(charger.require_rfid)

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
