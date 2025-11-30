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
from django.core.exceptions import DisallowedHost, ObjectDoesNotExist
from django.http.request import split_domain_port
from django.db.models import Q
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import CustomerAccount
from .models import PasskeyCredential, RFID, TOTPDeviceSettings
from . import temp_passwords


TOTP_DEVICE_NAME = "authenticator"


def get_user_totp_device(user):
    """Return the most appropriate authenticator device for the user."""

    devices = list(get_user_totp_devices(user))
    if not devices:
        return None
    if TOTP_DEVICE_NAME:
        named = [device for device in devices if device.name == TOTP_DEVICE_NAME]
        if named:
            return named[0]
    return devices[0]


def get_user_totp_devices(user):
    """Return confirmed authenticator devices available to the user."""

    group_ids = list(user.groups.values_list("id", flat=True))
    device_qs = TOTPDevice.objects.filter(confirmed=True, user=user)
    if group_ids:
        device_qs = TOTPDevice.objects.filter(confirmed=True).filter(
            Q(user=user) | Q(custom_settings__security_group_id__in=group_ids)
        )
    return device_qs.select_related("custom_settings").order_by("-id").distinct()


def totp_device_allows_passwordless(device):
    """Return True when the device can be used without a password."""

    if device is None:
        return False
    try:
        settings_obj = device.custom_settings
    except ObjectDoesNotExist:
        settings_obj = None
    if settings_obj is None:
        return False
    return bool(getattr(settings_obj, "allow_without_password", False))


def totp_device_requires_password(device):
    """Return True when the device requires a password alongside the OTP."""

    return not totp_device_allows_passwordless(device)


def totp_devices_require_password(devices, *, enforce=True):
    """Return True when any device requires a password alongside the OTP."""

    if not enforce:
        return False
    return any(totp_device_requires_password(device) for device in devices)


def totp_devices_allow_passwordless(devices):
    """Return True when any device allows passwordless authentication."""

    return any(totp_device_allows_passwordless(device) for device in devices)


def _get_or_clone_device_for_user(device, user, settings_obj):
    """Return a device bound to the provided user, cloning shared devices when needed."""

    existing = (
        TOTPDevice.objects.filter(
            user=user, confirmed=True, key=device.key, name=device.name
        )
        .order_by("-id")
        .first()
    )
    if existing:
        return existing

    cloned = TOTPDevice.objects.create(
        user=user,
        name=device.name,
        key=device.key,
        step=device.step,
        t0=device.t0,
        digits=device.digits,
        tolerance=device.tolerance,
        drift=device.drift,
        last_t=device.last_t,
        confirmed=True,
        throttling_failure_count=0,
        throttling_failure_timestamp=None,
    )

    if settings_obj is not None:
        TOTPDeviceSettings.objects.update_or_create(
            device=cloned,
            defaults={
                "issuer": settings_obj.issuer,
                "allow_without_password": settings_obj.allow_without_password,
                "security_group": settings_obj.security_group,
            },
        )

    return cloned


def verify_user_totp_token(
    user, token: str, password: str | None = None, *, enforce_password: bool | None = None
):
    """Verify a TOTP token against all of the user's available devices."""

    group_ids = set(user.groups.values_list("id", flat=True))
    devices = list(get_user_totp_devices(user))
    if not devices:
        return None, {"error": "missing_device", "requires_password": False}

    password_value = password or ""
    if enforce_password is None:
        enforce_password = bool(getattr(user, "require_2fa", False))
    password_valid = bool(password_value and user.check_password(password_value))
    requires_password = False

    for device in devices:
        device_requires_password = enforce_password or totp_device_requires_password(device)
        requires_password = requires_password or device_requires_password

        if device_requires_password and not password_valid:
            try:
                matches = device.verify_token(token)
            except Exception:
                matches = False

            if matches:
                if not password_value:
                    return None, {
                        "error": "password_required",
                        "requires_password": True,
                    }
                return None, {
                    "error": "invalid_password",
                    "requires_password": True,
                }
            continue

        try:
            verified = device.verify_token(token)
        except Exception:
            verified = False

        if verified:
            try:
                settings_obj = device.custom_settings
            except ObjectDoesNotExist:
                settings_obj = None
            if device.user_id != user.pk:
                security_group_id = getattr(settings_obj, "security_group_id", None)
                if security_group_id and security_group_id in group_ids:
                    device = _get_or_clone_device_for_user(device, user, settings_obj)
                else:
                    device.user = user
                    device.user_id = user.pk
            return device, {"requires_password": device_requires_password}

    return None, {"error": "invalid_token", "requires_password": requires_password}


class PasskeyBackend(ModelBackend):
    """Authenticate using a WebAuthn passkey credential."""

    def authenticate(self, request, credential_id=None, **kwargs):
        if not credential_id:
            return None

        credential_value = str(credential_id).strip()
        if not credential_value:
            return None

        try:
            passkey = PasskeyCredential.objects.select_related("user").get(
                credential_id=credential_value
            )
        except PasskeyCredential.DoesNotExist:
            return None

        user = passkey.user
        if not user.is_active:
            return None
        return user

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel._default_manager.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None


class TOTPBackend(ModelBackend):
    """Authenticate using a TOTP code from an enrolled authenticator app."""

    def authenticate(self, request, username=None, otp_token=None, password=None, **kwargs):
        if not username or otp_token in (None, ""):
            return None

        token = str(otp_token).strip().replace(" ", "")
        if not token:
            return None

        password = kwargs.get("password", password)

        UserModel = get_user_model()
        try:
            user = UserModel._default_manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            return None

        if not user.is_active:
            return None

        device, result = verify_user_totp_token(user, token, password)
        if device is None:
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
        if not self._is_admin_login_attempt(request, username, password):
            return super().authenticate(request, username, password, **kwargs)

        if not self._has_valid_host(request):
            return None

        remote_ip = self._get_remote_ip(request)
        if remote_ip is None or not self._is_remote_allowed(remote_ip):
            return None

        return self._get_admin_user()

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

        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        try:
            user = manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
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

    def get_user(self, user_id):
        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        try:
            return manager.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
