"""Custom authentication backends for the core app."""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import os
import socket
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import DisallowedHost
from django.http.request import split_domain_port
from django.db import DatabaseError

from apps.cards.actions import dispatch_rfid_action
from apps.cards.models import RFID
from apps.cards.models import RFIDAttempt
from apps.cards.reader import read_rfid_cell_value
from apps.energy.models import CustomerAccount
from apps.features.utils import is_suite_feature_enabled
from apps.totp.services import verify_any_totp
from . import temp_passwords
from .system import ensure_system_user

logger = logging.getLogger(__name__)
RFID_AUTH_AUDIT_FEATURE_SLUG = "rfid-auth-audit"


class PasswordOrOTPBackend(ModelBackend):
    """Authenticate using a password or a registered TOTP code."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        try:
            user = manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            return None

        if not self.user_can_authenticate(user):
            return None

        if self._verify_totp(user, password):
            return user

        if user.check_password(password):
            return user

        return None

    def _verify_totp(self, user, token: str) -> bool:
        digits_only = str(token).strip().replace(" ", "")
        if not digits_only.isdigit():
            return False

        return verify_any_totp(user, digits_only, confirmed_only=True)


class RFIDBackend:
    """Authenticate using a user's RFID."""

    @staticmethod
    def _audit_enabled() -> bool:
        """Return whether RFID auth audit recording is currently enabled."""

        return is_suite_feature_enabled(RFID_AUTH_AUDIT_FEATURE_SLUG, default=True)

    @classmethod
    def _record_auth_attempt(
        cls,
        *,
        rfid: str,
        status: str,
        reason_code: str | None = None,
        tag: RFID | None = None,
        account: CustomerAccount | None = None,
        metadata: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        """Persist RFID login attempt metadata when auth auditing is enabled."""

        if not cls._audit_enabled():
            return

        normalized_rfid = (rfid or "").strip().upper()
        if not normalized_rfid:
            return

        payload: dict[str, str | int | bool | None] = {
            "rfid": normalized_rfid,
            "audit_suite": True,
        }
        if tag is not None:
            payload["label_id"] = tag.pk
            payload["allowed"] = tag.allowed
        if reason_code:
            payload["reason_code"] = reason_code
        if metadata:
            payload.update(metadata)

        try:
            RFIDAttempt.record_attempt(
                payload,
                source=RFIDAttempt.Source.AUTH,
                status=status,
                authenticated=status == RFIDAttempt.Status.ACCEPTED,
                account_id=account.pk if account else None,
            )
        except DatabaseError:
            logger.warning("Unable to record RFID auth attempt", exc_info=True)

    @classmethod
    def _reject(
        cls,
        *,
        rfid: str,
        reason_code: str,
        tag: RFID | None = None,
        account: CustomerAccount | None = None,
        metadata: dict[str, str | int | bool | None] | None = None,
    ):
        """Record a rejected RFID auth attempt and return ``None``."""

        cls._record_auth_attempt(
            rfid=rfid,
            status=RFIDAttempt.Status.REJECTED,
            reason_code=reason_code,
            tag=tag,
            account=account,
            metadata=metadata,
        )
        return None

    @classmethod
    def _accept(
        cls,
        *,
        user,
        rfid: str,
        tag: RFID,
        account: CustomerAccount | None = None,
        metadata: dict[str, str | int | bool | None] | None = None,
    ):
        """Record an accepted RFID auth attempt and return the resolved user."""

        cls._record_auth_attempt(
            rfid=rfid,
            status=RFIDAttempt.Status.ACCEPTED,
            tag=tag,
            account=account,
            metadata=metadata,
        )
        return user

    def authenticate(self, request, rfid=None, **kwargs):
        if not rfid:
            return None
        rfid_value = str(rfid).strip().upper()
        if not rfid_value:
            return None

        matching_tags = RFID.matching_queryset(rfid_value)
        tag = matching_tags.filter(allowed=True).first()
        if not tag:
            blocked_tag = matching_tags.first()
            reason = (
                RFIDAttempt.Reason.TAG_NOT_ALLOWED
                if blocked_tag is not None
                else RFIDAttempt.Reason.TAG_NOT_FOUND
            )
            return self._reject(
                rfid=rfid_value,
                reason_code=reason,
                tag=blocked_tag,
            )

        update_fields: list[str] = []
        if tag.adopt_rfid(rfid_value):
            update_fields.append("rfid")
        if update_fields:
            tag.save(update_fields=update_fields)

        User = get_user_model()
        login_user = User.objects.filter(login_rfid=tag, is_active=True).first()
        if login_user:
            block = getattr(login_user, "login_rfid_block", None)
            offset = getattr(login_user, "login_rfid_offset", None)
            expected_value = (
                (getattr(login_user, "login_rfid_value", "") or "").strip().upper()
            )
            if block is not None and offset is not None and expected_value:
                key_choice = getattr(
                    login_user, "login_rfid_key", login_user.LOGIN_RFID_KEY_A
                )
                key_value = (
                    tag.key_a
                    if key_choice == login_user.LOGIN_RFID_KEY_A
                    else tag.key_b
                )
                result = read_rfid_cell_value(
                    block=block,
                    offset=offset,
                    key=key_value,
                    key_type=key_choice,
                )
                if result.get("error"):
                    return self._reject(
                        rfid=rfid_value,
                        reason_code=RFIDAttempt.Reason.READ_ERROR,
                        tag=tag,
                        metadata={"read_error": str(result.get("error"))[:128]},
                    )
                scanned_rfid = result.get("rfid")
                if scanned_rfid:
                    expected_rfid = (tag.rfid or rfid_value).strip().upper()
                    if expected_rfid and scanned_rfid.strip().upper() != expected_rfid:
                        return self._reject(
                            rfid=rfid_value,
                            reason_code=RFIDAttempt.Reason.RFID_MISMATCH,
                            tag=tag,
                            metadata={"expected_rfid": expected_rfid},
                        )
                cell_value = result.get("value")
                if cell_value is None:
                    return self._reject(
                        rfid=rfid_value,
                        reason_code=RFIDAttempt.Reason.READ_ERROR,
                        tag=tag,
                    )
                if f"{int(cell_value):02X}" != expected_value:
                    return self._reject(
                        rfid=rfid_value,
                        reason_code=RFIDAttempt.Reason.CELL_VALUE_MISMATCH,
                        tag=tag,
                    )
            return self._accept(
                user=login_user,
                rfid=rfid_value,
                tag=tag,
                metadata={"auth_path": "login_rfid"},
            )

        validation_action = dispatch_rfid_action(
            action_id=getattr(tag, "validation_action", ""),
            rfid=rfid_value,
            tag=tag,
            phase="auth_validation",
        )
        if not validation_action.success:
            return self._reject(
                rfid=rfid_value,
                reason_code=RFIDAttempt.Reason.EXTERNAL_COMMAND_ERROR,
                tag=tag,
                metadata={"action_error": validation_action.error[:128]},
            )

        account = (
            CustomerAccount.objects.filter(
                rfids__pk=tag.pk, rfids__allowed=True, user__isnull=False
            )
            .select_related("user")
            .first()
        )
        if account:
            post_action = dispatch_rfid_action(
                action_id=getattr(tag, "post_auth_action", ""),
                rfid=rfid_value,
                tag=tag,
                phase="auth_success",
            )
            if not post_action.success:
                logger.info(
                    "RFID post-auth action failed for label=%s: %s",
                    tag.pk,
                    post_action.error,
                )
            return self._accept(
                user=account.user,
                rfid=rfid_value,
                tag=tag,
                account=account,
                metadata={"auth_path": "customer_account"},
            )
        return self._reject(
            rfid=rfid_value,
            reason_code=RFIDAttempt.Reason.ACCOUNT_NOT_FOUND,
            tag=tag,
        )

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


def _collect_local_ip_addresses():
    """Return IP addresses assigned to the current machine."""

    hosts = {socket.gethostname().strip()}
    with contextlib.suppress(Exception):
        hosts.add(socket.getfqdn().strip())

    addresses = set()
    for host in filter(None, hosts):
        with contextlib.suppress(OSError):
            _, _, ip_list = socket.gethostbyname_ex(host)
            for candidate in ip_list:
                with contextlib.suppress(ValueError):
                    addresses.add(ipaddress.ip_address(candidate))
        with contextlib.suppress(OSError):
            for info in socket.getaddrinfo(host, None, family=socket.AF_UNSPEC):
                sockaddr = info[-1]
                if not sockaddr:
                    continue
                raw_address = sockaddr[0]
                if isinstance(raw_address, bytes):
                    with contextlib.suppress(UnicodeDecodeError):
                        raw_address = raw_address.decode()
                if isinstance(raw_address, str):
                    if "%" in raw_address:
                        raw_address = raw_address.split("%", 1)[0]
                    with contextlib.suppress(ValueError):
                        addresses.add(ipaddress.ip_address(raw_address))
    return tuple(sorted(addresses, key=str))


def _normalize_ip_candidate(candidate: str) -> str | None:
    """Normalize a raw socket IP candidate by stripping ports, brackets, and zones."""

    value = str(candidate or "").strip()
    if not value:
        return None

    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    elif ":" in value and value.count(":") == 1:
        host, port = split_domain_port(value)
        if host and port:
            value = host

    if "%" in value:
        value = value.split("%", 1)[0]

    return value or None


def _parse_forwarded_ip_candidate(
    candidate: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a forwarded IP candidate without relaxing malformed header tokens."""

    value = str(candidate or "").strip()
    if not value:
        return None

    if any(token in value for token in ("[", "]", "%")):
        return None

    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


class LocalhostAdminBackend(ModelBackend):
    """Allow default admin credentials only from local networks."""

    _ALLOWED_NETWORKS = (
        ipaddress.ip_network("10.42.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("172.17.0.0/16"),
        ipaddress.ip_network("172.18.0.0/16"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("::1/128"),
    )
    _CONTROL_ALLOWED_NETWORKS = (ipaddress.ip_network("10.0.0.0/8"),)
    _TRUSTED_PROXY_NETWORKS = (
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("172.17.0.0/16"),
        ipaddress.ip_network("172.18.0.0/16"),
        ipaddress.ip_network("::1/128"),
    )
    _LOCAL_IPS = _collect_local_ip_addresses()

    def _iter_allowed_networks(self):
        yield from self._ALLOWED_NETWORKS
        if getattr(settings, "NODE_ROLE", "") == "Control":
            yield from self._CONTROL_ALLOWED_NETWORKS

    def _iter_trusted_proxies(self):
        yield from self._TRUSTED_PROXY_NETWORKS

    def _iter_trusted_forwarded_proxies(self):
        configured = getattr(settings, "TRUSTED_PROXIES", ())
        if isinstance(configured, str):
            configured = (configured,)
        for value in configured:
            candidate = str(value).strip()
            if not candidate:
                continue
            try:
                if "/" in candidate:
                    yield ipaddress.ip_network(candidate, strict=False)
                else:
                    yield ipaddress.ip_network(candidate)
            except ValueError:
                continue

    def _is_test_environment(self, request) -> bool:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return True
        if any(arg == "test" for arg in sys.argv):
            return True
        executable = os.path.basename(sys.argv[0]) if sys.argv else ""
        if executable in {"pytest", "py.test"}:
            return True
        server_name = ""
        if request is not None:
            try:
                server_name = request.META.get("SERVER_NAME", "")
            except Exception:
                server_name = ""
        return server_name.endswith(".local")

    def user_can_authenticate(self, user):
        return True

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        if self._is_admin_login_attempt(request, username, password):
            if not self._has_valid_host(request):
                return None

            remote_ip = self._get_remote_ip(request)
            if remote_ip is None or not self._is_remote_allowed(remote_ip):
                return None

            user = self._get_admin_user()
            if user is None:
                return None
            user.backend = f"{self.__module__}.{self.__class__.__name__}"
            return user

        return None

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.all_objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    def _is_admin_login_attempt(self, request, username, password) -> bool:
        return request is not None and username == "admin" and password == "admin"

    def _has_valid_host(self, request) -> bool:
        try:
            host = request.get_host()
        except DisallowedHost:
            return False

        host, _port = split_domain_port(host)
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if host.lower() == "localhost":
            host = "127.0.0.1"

        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return self._is_test_environment(request)

    def _get_remote_ip(self, request):
        """Return the originating client IP, honoring trusted proxy chains only."""

        remote = _normalize_ip_candidate(
            request.META.get("REMOTE_ADDR", "") if request else ""
        )
        if remote is None:
            return None

        try:
            remote_ip = ipaddress.ip_address(remote)
        except ValueError:
            return None

        trusted_proxies = tuple(self._iter_trusted_proxies())
        if not any(remote_ip in proxy for proxy in trusted_proxies):
            return remote_ip

        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "") if request else ""
        if not forwarded:
            return remote_ip

        trusted_forwarded_proxies = tuple(self._iter_trusted_forwarded_proxies())
        for candidate in reversed([value.strip() for value in forwarded.split(",")]):
            candidate_ip = _parse_forwarded_ip_candidate(candidate)
            if candidate_ip is None:
                continue
            if any(candidate_ip in proxy for proxy in trusted_forwarded_proxies):
                continue
            return candidate_ip

        return remote_ip

    def _is_remote_allowed(self, ip):
        if any(ip in net for net in self._iter_allowed_networks()):
            return True
        if ip in self._LOCAL_IPS:
            return True
        return False

    def _get_admin_user(self):
        User = get_user_model()
        system_user = ensure_system_user()
        user, created = User.all_objects.get_or_create(
            username="admin",
            defaults={
                "is_staff": True,
                "is_superuser": True,
            },
        )

        if not created and not user.is_active:
            return None

        arthexis_user = (
            User.all_objects.filter(username=User.SYSTEM_USERNAME)
            .exclude(pk=user.pk)
            .first()
        )
        if arthexis_user is None:
            arthexis_user = system_user

        if created:
            if arthexis_user and user.operate_as_id is None:
                user.operate_as = arthexis_user
            user.set_password("admin")
            user.save()
            return user

        if not user.check_password("admin"):
            if not user.password or not user.has_usable_password():
                user.set_password("admin")
                user.save(update_fields=["password"])
            else:
                return None

        if arthexis_user and user.operate_as_id is None:
            user.operate_as = arthexis_user
            user.save(update_fields=["operate_as"])

        return user


class AccessPointLocalUserBackend(LocalhostAdminBackend):
    """Allow selected non-staff users to sign in from local IPv4 /16 peers."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if request is None or username is None:
            return None

        if not self._has_valid_host(request):
            return None

        remote_ip = self._get_remote_ip(request)
        if remote_ip is None or not self._is_remote_allowed(remote_ip):
            return None

        user = self._resolve_user(username)
        if user is None or not self._is_access_point_candidate(user):
            return None

        user.backend = f"{self.__module__}.{self.__class__.__name__}"
        return user

    def _resolve_user(self, username):
        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        normalized = str(username).strip()
        if not normalized:
            return None
        try:
            user = manager.get_by_natural_key(normalized)
        except UserModel.DoesNotExist:
            user = (
                manager.filter(email__iexact=normalized).order_by("pk").first()
            )
        return user

    def _is_access_point_candidate(self, user) -> bool:
        if not ModelBackend.user_can_authenticate(self, user):
            return False
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return False
        return bool(
            getattr(user, "allow_local_network_passwordless_login", False)
        )

    def _is_remote_allowed(self, ip):
        if isinstance(ip, ipaddress.IPv6Address):
            return ip.is_loopback
        if not ip.is_private and not ip.is_loopback:
            return False
        if ip.is_loopback:
            return True

        remote_octets = ip.exploded.split(".")
        if len(remote_octets) != 4:
            return False

        for local_ip in self._LOCAL_IPS:
            if not isinstance(local_ip, ipaddress.IPv4Address):
                continue
            if not local_ip.is_private and not local_ip.is_loopback:
                continue
            local_octets = local_ip.exploded.split(".")
            if local_octets[:2] == remote_octets[:2]:
                return True
        return False


class TempPasswordBackend(ModelBackend):
    """Authenticate using a temporary password stored in a lockfile."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        normalized_username = str(username).strip()

        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        try:
            user = manager.get_by_natural_key(normalized_username)
        except UserModel.DoesNotExist:
            user = (
                manager.filter(email__iexact=normalized_username).order_by("pk").first()
            )
            if user is None:
                return None

        is_expired = getattr(user, "is_temporarily_expired", None)
        if is_expired and (is_expired() if callable(is_expired) else is_expired):
            deactivate = getattr(user, "deactivate_temporary_credentials", None)
            if callable(deactivate):
                deactivate()
            return None

        entry = temp_passwords.load_temp_password(user.username)
        if entry is None:
            return None
        if entry.is_expired:
            temp_passwords.discard_temp_password(user.username)
            deactivate = getattr(user, "deactivate_temporary_credentials", None)
            if callable(deactivate):
                deactivate()
            return None
        if not entry.check_password(password):
            return None

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        return user
