"""Custom authentication backends for the core app."""

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
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import CustomerAccount, RFID
from . import temp_passwords


TOTP_DEVICE_NAME = "authenticator"


class TOTPBackend(ModelBackend):
    """Authenticate using a TOTP code from an enrolled authenticator app."""

    def authenticate(self, request, username=None, otp_token=None, **kwargs):
        if not username or otp_token in (None, ""):
            return None

        token = str(otp_token).strip().replace(" ", "")
        if not token:
            return None

        UserModel = get_user_model()
        try:
            user = UserModel._default_manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            return None

        if not user.is_active:
            return None

        device_qs = TOTPDevice.objects.filter(user=user, confirmed=True)
        if TOTP_DEVICE_NAME:
            device = device_qs.filter(name=TOTP_DEVICE_NAME).order_by("-id").first()
        else:
            device = None

        if device is None:
            device = device_qs.order_by("-id").first()
        if device is None:
            return None

        try:
            verified = device.verify_token(token)
        except Exception:
            return None

        if not verified:
            return None

        user.otp_device = device
        return user

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel._default_manager.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None


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
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("10.42.0.0/16"),
        ipaddress.ip_network("192.168.0.0/16"),
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
            server_name = request.META.get("SERVER_NAME", "")
        return server_name.lower() == "testserver"

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username == "admin" and password == "admin" and request is not None:
            try:
                host = request.get_host()
            except DisallowedHost:
                return None
            host, _port = split_domain_port(host)
            if host.startswith("[") and host.endswith("]"):
                host = host[1:-1]
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if host.lower() == "localhost":
                    host = "127.0.0.1"
                elif not self._is_test_environment(request):
                    return None
            forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
            if forwarded:
                remote = forwarded.split(",")[0].strip()
            else:
                remote = request.META.get("REMOTE_ADDR", "")
            try:
                ip = ipaddress.ip_address(remote)
            except ValueError:
                return None
            allowed = any(ip in net for net in self._iter_allowed_networks())
            if not allowed and ip in self._LOCAL_IPS:
                allowed = True
            if not allowed:
                return None
            User = get_user_model()
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
                User.all_objects.filter(username="arthexis").exclude(pk=user.pk).first()
            )
            if created:
                if arthexis_user and user.operate_as_id is None:
                    user.operate_as = arthexis_user
                user.set_password("admin")
                user.save()
            else:
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
        return super().authenticate(request, username, password, **kwargs)

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.all_objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


class TempPasswordBackend(ModelBackend):
    """Authenticate using a temporary password stored in a lockfile."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        try:
            user = manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            return None

        entry = temp_passwords.load_temp_password(user.username)
        if entry is None:
            return None
        if entry.is_expired:
            temp_passwords.discard_temp_password(user.username)
            return None
        if not entry.check_password(password):
            return None

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        return user

    def get_user(self, user_id):
        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        try:
            return manager.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
