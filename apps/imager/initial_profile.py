"""Validated, idempotent first-boot configuration for private Pi profiles."""

from __future__ import annotations

import ipaddress
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.db import transaction

from apps.cards.initial_profile import (
    InitialProfileError,
    load_initial_profile_data,
    load_pre_registered_rfids,
)
from apps.cards.models import RFID
from apps.ocpp import auto_start
from apps.ocpp.auto_start_accounts import (
    get_or_create_auto_start_account,
    get_or_create_rfid_fallback_account,
)
from apps.ocpp.models import Charger, ChargingStation

DEFAULT_REDIRECT_TABLE = "arthexis_ocpp_redirect"
DEFAULT_REDIRECT_TARGET_PORT = 80
DEFAULT_REDIRECT_LISTEN_PORT = 8888
REDIRECT_CONFIG_PATH = Path("/etc/arthexis/ocpp-redirect.nft")
REDIRECT_SERVICE_PATH = Path("/etc/systemd/system/arthexis-ocpp-redirect.service")
REDIRECT_SERVICE_NAME = "arthexis-ocpp-redirect.service"
_INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
NODE_RFID_LABEL_BLOCK_SIZE = 1000
MAX_RFID_LABEL_ID = 2_147_483_647
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChargerProfile:
    """The known field charger that should be configured at first boot."""

    charger_id: str
    path: str
    connectors: tuple[int, ...]


@dataclass(frozen=True)
class RedirectProfile:
    """A deliberately scoped ingress redirect for one field charger."""

    interface: str
    charger_ip: str
    targets: tuple[str, ...]
    target_port: int
    listen_port: int
    table: str

    def ruleset(self) -> str:
        """Render the nftables ruleset without applying it."""

        target_expression = (
            self.targets[0]
            if len(self.targets) == 1
            else "{ " + ", ".join(self.targets) + " }"
        )
        return (
            f"table ip {self.table} {{\n"
            "  chain prerouting {\n"
            "    type nat hook prerouting priority dstnat - 10; policy accept;\n"
            f'    iifname "{self.interface}" ip saddr {self.charger_ip} '
            f"ip daddr {target_expression} tcp dport {self.target_port} "
            f"counter redirect to :{self.listen_port}\n"
            "  }\n"
            "}\n"
        )


@dataclass(frozen=True)
class RedirectSnapshot:
    """The redirect state needed to compensate a failed profile application."""

    ruleset: str
    config: bytes | None
    config_mode: int | None
    service: bytes | None
    service_mode: int | None
    service_enabled: bool


@dataclass(frozen=True)
class InitialProfile:
    """All supported private image settings after validation."""

    rfids: tuple[str, ...]
    rfid_fallback_account: bool
    node_number: int | None
    host_network_names: tuple[str, ...]
    charger: ChargerProfile | None
    auto_start_id_tag: str
    redirect: RedirectProfile | None


@dataclass(frozen=True)
class InitialProfileResult:
    """Counts emitted after first-boot reconciliation without exposing secrets."""

    rfids_created: int
    rfids_existing: int
    chargers_created: int
    chargers_existing: int
    auto_start_account_created: bool
    fallback_account_created: bool
    fallback_cards_bound: int
    redirect_applied: bool


def _table(profile: dict[str, object], name: str) -> dict[str, object] | None:
    value = profile.get(name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InitialProfileError(f"Initial profile [{name}] section must be a table.")
    return value


def _string_list(
    value: object, *, field_name: str, required: bool = False
) -> tuple[str, ...]:
    if value is None:
        if required:
            raise InitialProfileError(f"Initial profile {field_name} is required.")
        return ()
    if not isinstance(value, list):
        raise InitialProfileError(f"Initial profile {field_name} must be an array.")
    values: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            raise InitialProfileError(
                f"Initial profile {field_name} values must be non-empty strings."
            )
        normalized = raw.strip()
        if normalized not in values:
            values.append(normalized)
    if required and not values:
        raise InitialProfileError(f"Initial profile {field_name} must not be empty.")
    return tuple(values)


def _positive_port(value: object, *, field_name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise InitialProfileError(f"Initial profile {field_name} must be a TCP port.")
    port = value
    if not 1 <= port <= 65535:
        raise InitialProfileError(
            f"Initial profile {field_name} must be between 1 and 65535."
        )
    return port


def _ipv4(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InitialProfileError(
            f"Initial profile {field_name} must be an IPv4 address."
        )
    try:
        parsed = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise InitialProfileError(
            f"Initial profile {field_name} must be an IPv4 address."
        ) from exc
    if parsed.version != 4:
        raise InitialProfileError(f"Initial profile {field_name} must be IPv4.")
    return str(parsed)


def _parse_node(profile: dict[str, object]) -> int | None:
    section = _table(profile, "node")
    if section is None:
        return None
    if set(section) - {"number"}:
        raise InitialProfileError("Initial profile [node] only supports number.")
    number = section.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise InitialProfileError(
            "Initial profile node.number must be a positive integer."
        )
    return number


def _parse_network(profile: dict[str, object]) -> tuple[str, ...]:
    section = _table(profile, "network")
    if section is None:
        return ()
    if set(section) - {"copy_host_profiles"}:
        raise InitialProfileError(
            "Initial profile [network] only supports copy_host_profiles."
        )
    return _string_list(
        section.get("copy_host_profiles"), field_name="network.copy_host_profiles"
    )


def _parse_rfid_fallback(profile: dict[str, object]) -> bool:
    section = _table(profile, "rfid")
    if (
        section is None
    ):  # load_pre_registered_rfids gives the clearer required-table error
        return False
    if set(section) - {"pre_register", "fallback_account"}:
        raise InitialProfileError(
            "Initial profile [rfid] only supports pre_register and fallback_account."
        )
    fallback_account = section.get("fallback_account", False)
    if not isinstance(fallback_account, bool):
        raise InitialProfileError(
            "Initial profile rfid.fallback_account must be true or false."
        )
    return fallback_account


def _parse_charger(profile: dict[str, object]) -> ChargerProfile | None:
    section = _table(profile, "charger")
    if section is None:
        return None
    allowed = {"id", "path", "connectors"}
    if set(section) - allowed:
        raise InitialProfileError(
            "Initial profile [charger] supports id, path, and connectors only."
        )
    charger_id = section.get("id")
    if not isinstance(charger_id, str) or not charger_id.strip():
        raise InitialProfileError(
            "Initial profile charger.id must be a non-empty string."
        )
    try:
        normalized_id = Charger.validate_serial(charger_id.strip())
    except ValidationError as exc:
        raise InitialProfileError("Initial profile charger.id is invalid.") from exc
    path = section.get("path")
    if not isinstance(path, str) or not path.strip().startswith("/"):
        raise InitialProfileError(
            "Initial profile charger.path must be an absolute path."
        )
    normalized_path = path.strip().split("?", 1)[0].split("#", 1)[0]
    if normalized_path.rstrip("/").rsplit("/", 1)[-1] != normalized_id:
        raise InitialProfileError(
            "Initial profile charger.path final segment must match charger.id."
        )
    connectors_value = section.get("connectors", [1])
    if not isinstance(connectors_value, list) or not connectors_value:
        raise InitialProfileError(
            "Initial profile charger.connectors must be a non-empty array."
        )
    connectors: list[int] = []
    for raw in connectors_value:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise InitialProfileError(
                "Initial profile charger.connectors must contain positive integers."
            )
        if raw not in connectors:
            connectors.append(raw)
    return ChargerProfile(normalized_id, normalized_path, tuple(connectors))


def _parse_auto_start(
    profile: dict[str, object], charger: ChargerProfile | None
) -> str:
    section = _table(profile, "auto_start")
    if section is None:
        return ""
    if set(section) - {"id_tag"}:
        raise InitialProfileError("Initial profile [auto_start] only supports id_tag.")
    if charger is None:
        raise InitialProfileError("Initial profile [auto_start] requires [charger].")
    try:
        id_tag = Charger.normalize_auto_start_id_tag(section.get("id_tag"))
    except ValidationError as exc:
        raise InitialProfileError(
            "Initial profile auto_start.id_tag is invalid."
        ) from exc
    if not id_tag:
        raise InitialProfileError(
            "Initial profile auto_start.id_tag must be non-empty."
        )
    return id_tag


def _parse_redirect(profile: dict[str, object]) -> RedirectProfile | None:
    section = _table(profile, "ocpp_redirect")
    if section is None:
        return None
    allowed = {"interface", "charger_ip", "targets", "target_port", "listen_port"}
    if set(section) - allowed:
        raise InitialProfileError(
            "Initial profile [ocpp_redirect] contains unsupported settings."
        )
    interface = section.get("interface", "eth0")
    if not isinstance(interface, str) or not _INTERFACE_RE.match(interface.strip()):
        raise InitialProfileError("Initial profile ocpp_redirect.interface is invalid.")
    targets = tuple(
        _ipv4(target, field_name="ocpp_redirect.targets")
        for target in _string_list(
            section.get("targets"),
            field_name="ocpp_redirect.targets",
            required=True,
        )
    )
    return RedirectProfile(
        interface=interface.strip(),
        charger_ip=_ipv4(
            section.get("charger_ip"), field_name="ocpp_redirect.charger_ip"
        ),
        targets=targets,
        target_port=_positive_port(
            section.get("target_port"),
            field_name="ocpp_redirect.target_port",
            default=DEFAULT_REDIRECT_TARGET_PORT,
        ),
        listen_port=_positive_port(
            section.get("listen_port"),
            field_name="ocpp_redirect.listen_port",
            default=DEFAULT_REDIRECT_LISTEN_PORT,
        ),
        table=DEFAULT_REDIRECT_TABLE,
    )


def load_initial_profile(profile_path: Path) -> InitialProfile:
    """Load every supported initial-profile section before changing an image or node."""

    profile = load_initial_profile_data(profile_path)
    allowed_sections = {
        "rfid",
        "node",
        "network",
        "charger",
        "auto_start",
        "ocpp_redirect",
    }
    unknown_sections = set(profile) - allowed_sections
    if unknown_sections:
        raise InitialProfileError(
            "Initial profile contains unsupported section(s): "
            + ", ".join(sorted(unknown_sections))
        )
    rfids = load_pre_registered_rfids(profile_path)
    node_number = _parse_node(profile)
    _validate_node_rfid_label_range(node_number, rfids)
    charger = _parse_charger(profile)
    auto_start_id_tag = _parse_auto_start(profile, charger)
    if auto_start_id_tag:
        normalized_auto_start_rfid = RFID.normalize_code(auto_start_id_tag)
        auto_start_rfid_candidates = {
            normalized_auto_start_rfid,
            RFID.reverse_uid(normalized_auto_start_rfid),
        }
        if set(rfids) & (auto_start_rfid_candidates - {""}):
            raise InitialProfileError(
                "Initial profile auto_start.id_tag conflicts with an RFID pre_register value."
            )
    return InitialProfile(
        rfids=rfids,
        rfid_fallback_account=_parse_rfid_fallback(profile),
        node_number=node_number,
        host_network_names=_parse_network(profile),
        charger=charger,
        auto_start_id_tag=auto_start_id_tag,
        redirect=_parse_redirect(profile),
    )


def _node_rfid_label_id(node_number: int | None, position: int) -> int | None:
    """Return the node-scoped label for a pre-registered RFID position."""

    if node_number is None:
        return None
    return (node_number * NODE_RFID_LABEL_BLOCK_SIZE) + (
        position * RFID.SCAN_LABEL_STEP
    )


def _validate_node_rfid_label_range(
    node_number: int | None, rfids: tuple[str, ...]
) -> None:
    """Keep a node's generated labels inside its thousand-value block."""

    if node_number is None:
        return
    maximum = NODE_RFID_LABEL_BLOCK_SIZE // RFID.SCAN_LABEL_STEP
    if len(rfids) > maximum:
        raise InitialProfileError(
            "Initial profile has more than "
            f"{maximum} RFID pre_register values for node {node_number}."
        )
    if rfids:
        maximum_label = _node_rfid_label_id(node_number, len(rfids) - 1)
        if maximum_label is not None and maximum_label > MAX_RFID_LABEL_ID:
            raise InitialProfileError(
                "Initial profile node RFID labels exceed the supported maximum."
            )


def _reconcile_rfids(
    rfids: tuple[str, ...], *, node_number: int | None
) -> tuple[int, int, tuple[RFID, ...]]:
    created = 0
    existing = 0
    cards: list[RFID] = []
    for position, rfid in enumerate(rfids):
        desired_label_id = _node_rfid_label_id(node_number, position)
        card = RFID.objects.filter(rfid=rfid).first()
        if card is None and desired_label_id is not None:
            conflict = RFID.objects.filter(label_id=desired_label_id).first()
            if conflict is not None:
                raise InitialProfileError(
                    "Initial profile RFID label "
                    f"{desired_label_id} is already assigned to a different card."
                )
        if card is None:
            create_kwargs: dict[str, object] = {"rfid": rfid}
            if desired_label_id is not None:
                create_kwargs["label_id"] = desired_label_id
            card = RFID.objects.create(**create_kwargs)
            was_created = True
        else:
            was_created = False
        cards.append(card)
        if was_created:
            created += 1
        else:
            existing += 1
    return created, existing, tuple(cards)


def _reset_node_rfid_label_sequence() -> None:
    """Repair explicit-label sequence state without undoing committed profiles."""

    try:
        RFID._reset_label_sequence()
    except Exception:
        logger.exception(
            "Initial profile could not reset the RFID label sequence after commit; "
            "a later profile application will retry it."
        )


def _reconcile_rfid_fallback_account(cards: tuple[RFID, ...]) -> tuple[bool, int]:
    account, created = get_or_create_rfid_fallback_account()
    bindable_cards = tuple(
        card
        for card in cards
        if not card.energy_accounts.exists()
        or card.energy_accounts.filter(pk=account.pk).exists()
    )
    account.rfids.add(*bindable_cards)
    return created, len(bindable_cards)


def _reconcile_chargers(
    charger_profile: ChargerProfile, auto_start_id_tag: str
) -> tuple[int, int]:
    station, _station_created = ChargingStation.objects.get_or_create(
        station_id=charger_profile.charger_id,
        defaults={"last_path": charger_profile.path},
    )
    if not station.last_path:
        station.last_path = charger_profile.path
        station.save(update_fields=["last_path"])

    created = 0
    existing = 0
    charger_ids: list[int] = []
    for connector_id in (None, *charger_profile.connectors):
        defaults = {
            "charging_station": station,
            "last_path": charger_profile.path,
            "authorization_policy": Charger.AuthorizationPolicy.STRICT,
            "require_rfid": True,
            "auto_start_id_tag": auto_start_id_tag,
        }
        charger, was_created = Charger.objects.get_or_create(
            charger_id=charger_profile.charger_id,
            connector_id=connector_id,
            defaults=defaults,
        )
        if was_created:
            created += 1
        else:
            existing += 1
            if (
                auto_start_id_tag
                and charger.auto_start_id_tag
                and charger.auto_start_id_tag != auto_start_id_tag
            ):
                raise InitialProfileError(
                    "Initial profile auto-start idTag conflicts with existing charger configuration."
                )
            updates: list[str] = []
            if charger.charging_station_id is None:
                charger.charging_station = station
                updates.append("charging_station")
            if not charger.last_path:
                charger.last_path = charger_profile.path
                updates.append("last_path")
            if auto_start_id_tag and not charger.auto_start_id_tag:
                charger.auto_start_id_tag = auto_start_id_tag
                updates.append("auto_start_id_tag")
            if updates:
                charger.save(update_fields=updates)
        charger_ids.append(charger.pk)
    if auto_start_id_tag:
        auto_start.release_chargers(charger_ids=charger_ids)
    return created, existing


def _write_redirect_service(redirect: RedirectProfile) -> None:
    REDIRECT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    REDIRECT_CONFIG_PATH.write_text(redirect.ruleset(), encoding="utf-8")
    REDIRECT_CONFIG_PATH.chmod(0o600)
    REDIRECT_SERVICE_PATH.write_text(
        "[Unit]\n"
        "Description=Arthexis scoped OCPP field redirect\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=/usr/sbin/nft -f {REDIRECT_CONFIG_PATH}\n"
        f"ExecStop=/usr/sbin/nft delete table ip {redirect.table}\n"
        "RemainAfterExit=yes\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    REDIRECT_SERVICE_PATH.chmod(0o644)


def _run_nft(
    *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/sbin/nft", *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _validate_redirect(redirect: RedirectProfile) -> None:
    """Check that the target system can install the scoped redirect."""

    ruleset = redirect.ruleset()
    check = _run_nft("-c", "-f", "-", input_text=ruleset)
    if check.returncode:
        raise InitialProfileError(
            "Initial profile OCPP redirect failed nft validation: "
            + (check.stderr or check.stdout).strip()
        )


def _read_redirect_file(path: Path) -> tuple[bytes | None, int | None]:
    if not path.exists():
        return None, None
    return path.read_bytes(), path.stat().st_mode & 0o777


def _snapshot_redirect(redirect: RedirectProfile) -> RedirectSnapshot:
    """Capture the current redirect so a later database failure can restore it."""

    listed = _run_nft("list", "table", "ip", redirect.table)
    if listed.returncode and "No such file" not in (listed.stderr or ""):
        raise InitialProfileError(
            "Initial profile could not snapshot the existing OCPP redirect: "
            + (listed.stderr or listed.stdout).strip()
        )
    config, config_mode = _read_redirect_file(REDIRECT_CONFIG_PATH)
    service, service_mode = _read_redirect_file(REDIRECT_SERVICE_PATH)
    enabled = subprocess.run(
        ["systemctl", "is-enabled", REDIRECT_SERVICE_NAME],
        text=True,
        capture_output=True,
        check=False,
    )
    return RedirectSnapshot(
        ruleset=listed.stdout if not listed.returncode else "",
        config=config,
        config_mode=config_mode,
        service=service,
        service_mode=service_mode,
        service_enabled=enabled.returncode == 0,
    )


def _restore_redirect_file(path: Path, content: bytes | None, mode: int | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode if mode is not None else 0o600)


def _restore_redirect(redirect: RedirectProfile, snapshot: RedirectSnapshot) -> None:
    """Restore the redirect table and service state captured before application."""

    _run_nft("delete", "table", "ip", redirect.table)
    if snapshot.ruleset:
        restored = _run_nft("-f", "-", input_text=snapshot.ruleset)
        if restored.returncode:
            raise InitialProfileError(
                "Initial profile could not restore the previous OCPP redirect: "
                + (restored.stderr or restored.stdout).strip()
            )
    if not snapshot.service_enabled:
        subprocess.run(
            ["systemctl", "disable", REDIRECT_SERVICE_NAME],
            text=True,
            capture_output=True,
            check=False,
        )
    _restore_redirect_file(REDIRECT_CONFIG_PATH, snapshot.config, snapshot.config_mode)
    _restore_redirect_file(
        REDIRECT_SERVICE_PATH, snapshot.service, snapshot.service_mode
    )
    reloaded = subprocess.run(
        ["systemctl", "daemon-reload"], text=True, capture_output=True, check=False
    )
    if reloaded.returncode:
        raise InitialProfileError("Initial profile could not restore systemd units.")
    if snapshot.service_enabled:
        restored_service = subprocess.run(
            ["systemctl", "enable", REDIRECT_SERVICE_NAME],
            text=True,
            capture_output=True,
            check=False,
        )
        if restored_service.returncode:
            raise InitialProfileError(
                "Initial profile could not restore redirect service state."
            )


def _apply_redirect(redirect: RedirectProfile) -> RedirectSnapshot:
    """Install a validated redirect and return state for possible compensation."""

    snapshot = _snapshot_redirect(redirect)
    ruleset = redirect.ruleset()
    try:
        _run_nft("delete", "table", "ip", redirect.table)
        applied = _run_nft("-f", "-", input_text=ruleset)
        if applied.returncode:
            raise InitialProfileError(
                "Initial profile OCPP redirect could not be applied: "
                + (applied.stderr or applied.stdout).strip()
            )
        _write_redirect_service(redirect)
        enabled = subprocess.run(
            ["systemctl", "daemon-reload"],
            text=True,
            capture_output=True,
            check=False,
        )
        if enabled.returncode:
            raise InitialProfileError("Initial profile could not reload systemd units.")
        enabled = subprocess.run(
            ["systemctl", "enable", REDIRECT_SERVICE_NAME],
            text=True,
            capture_output=True,
            check=False,
        )
        if enabled.returncode:
            raise InitialProfileError(
                "Initial profile could not enable OCPP redirect service."
            )
    except Exception as exc:
        try:
            _restore_redirect(redirect, snapshot)
        except InitialProfileError as restore_error:
            raise InitialProfileError(
                f"{exc} Previous redirect restoration also failed: {restore_error}"
            ) from exc
        if not isinstance(exc, InitialProfileError):
            raise InitialProfileError(
                "Initial profile could not install the OCPP redirect."
            ) from exc
        raise
    return snapshot


def reconcile_initial_profile(profile_path: Path) -> InitialProfileResult:
    """Apply a validated profile without overwriting pre-existing user configuration."""

    profile = load_initial_profile(profile_path)
    # nft validation needs kernel capabilities and must happen before database
    # reconciliation so an incapable host cannot retain partial profile state.
    if profile.redirect is not None:
        _validate_redirect(profile.redirect)
    redirect_snapshot = _apply_redirect(profile.redirect) if profile.redirect else None
    try:
        fallback_account_created = False
        fallback_cards_bound = 0
        account_created = False
        chargers_created = 0
        chargers_existing = 0
        with transaction.atomic():
            rfids_created, rfids_existing, cards = _reconcile_rfids(
                profile.rfids, node_number=profile.node_number
            )
            if profile.rfid_fallback_account:
                fallback_account_created, fallback_cards_bound = (
                    _reconcile_rfid_fallback_account(cards)
                )
            if profile.auto_start_id_tag:
                _account, account_created = get_or_create_auto_start_account(
                    profile.auto_start_id_tag
                )
            if profile.charger is not None:
                chargers_created, chargers_existing = _reconcile_chargers(
                    profile.charger, profile.auto_start_id_tag
                )
            if profile.node_number is not None and profile.rfids:
                transaction.on_commit(_reset_node_rfid_label_sequence)
    except Exception as exc:
        if redirect_snapshot is not None:
            try:
                _restore_redirect(profile.redirect, redirect_snapshot)
            except InitialProfileError as restore_error:
                raise InitialProfileError(
                    "Initial profile database reconciliation failed and the previous "
                    f"OCPP redirect could not be restored: {restore_error}"
                ) from exc
        raise
    return InitialProfileResult(
        rfids_created=rfids_created,
        rfids_existing=rfids_existing,
        chargers_created=chargers_created,
        chargers_existing=chargers_existing,
        auto_start_account_created=account_created,
        fallback_account_created=fallback_account_created,
        fallback_cards_bound=fallback_cards_bound,
        redirect_applied=profile.redirect is not None,
    )
