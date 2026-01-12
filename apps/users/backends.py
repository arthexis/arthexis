"""Custom authentication backends for the core app."""

from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
import subprocess
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import DisallowedHost
from django.http.request import split_domain_port

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from . import temp_passwords
from .system import ensure_system_user


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

        try:
            from apps.totp.models import TOTPDevice
        except Exception:
            return False

        return TOTPDevice.verify_any(user, digits_only, confirmed_only=True)


class RFIDBackend:
    """Authenticate using a user's RFID."""

    def authenticate(self, request, rfid=None, **kwargs):
        if not rfid:
            return None
        rfid_value = str(rfid).strip().upper()
        if not rfid_value:
            return None

        tag = RFID.matching_queryset(rfid_value).filter(allowed=True).first()
        if not tag:
            return None

        update_fields: list[str] = []
        if tag.adopt_rfid(rfid_value):
            update_fields.append("rfid")
        if update_fields:
            tag.save(update_fields=update_fields)

        command = (tag.external_command or "").strip()
        if command:
            env = os.environ.copy()
            env["RFID_VALUE"] = rfid_value
            env["RFID_LABEL_ID"] = str(tag.pk)
            env["RFID_ENDIANNESS"] = getattr(tag, "endianness", RFID.BIG_ENDIAN)
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )
            except Exception:
                return None
            if completed.returncode != 0:
                return None

        account = (
            CustomerAccount.objects.filter(
                rfids__pk=tag.pk, rfids__allowed=True, user__isnull=False
            )
            .select_related("user")
            .first()
        )
        if account:
            post_command = (getattr(tag, "post_auth_command", "") or "").strip()
            if post_command:
                env = os.environ.copy()
                env["RFID_VALUE"] = rfid_value
                env["RFID_LABEL_ID"] = str(tag.pk)
                env["RFID_ENDIANNESS"] = getattr(tag, "endianness", RFID.BIG_ENDIAN)
                with contextlib.suppress(Exception):
                    subprocess.Popen(
                        post_command,
                        shell=True,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            return account.user
        return None

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
    _LOCAL_IPS = _collect_local_ip_addresses()

    def _iter_allowed_networks(self):
        yield from self._ALLOWED_NETWORKS
        if getattr(settings, "NODE_ROLE", "") == "Control":
            yield from self._CONTROL_ALLOWED_NETWORKS

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
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR") if request else ""
        if forwarded:
            remote = forwarded.split(",")[0].strip()
        else:
            remote = request.META.get("REMOTE_ADDR", "") if request else ""

        try:
            return ipaddress.ip_address(remote)
        except ValueError:
            return None

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
                manager.filter(email__iexact=normalized_username)
                .order_by("pk")
                .first()
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
