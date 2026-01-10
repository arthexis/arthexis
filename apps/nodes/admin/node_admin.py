from collections import OrderedDict
from collections.abc import Mapping
import ipaddress
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
import base64
import binascii
import json
import uuid

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.response import TemplateResponse
from django.test import signals
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from requests import RequestException
import requests

from apps.nodes.logging import get_register_visitor_logger

from apps.cards.models import RFID
from apps.cards.sync import apply_rfid_payload
from apps.core.admin import SaveBeforeChangeAction
from apps.locals.user_data import EntityModelAdmin
from apps.ocpp import store
from apps.ocpp.models import (
    Charger,
    CPFirmware,
    CPFirmwareDeployment,
    CPFirmwareRequest,
    DataTransferMessage,
)
from apps.ocpp.network import serialize_charger_for_network
from apps.users import temp_passwords

from ..models import Node, NodeRole, _format_upgrade_body
from .actions import (
    create_charge_point_forwarder,
    download_evcs_firmware,
    export_rfids_to_selected,
    import_rfids_from_selected,
    register_visitor,
    run_task,
    send_net_message,
    take_screenshots,
    update_selected_nodes,
)
from .forms import NodeAdminForm
from .inlines import NodeFeatureAssignmentInline, SSHAccountInline


registration_logger = get_register_visitor_logger()


@admin.register(Node)
class NodeAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    list_display = (
        "hostname",
        "primary_ip",
        "port",
        "mac_address_display",
        "role",
        "relation",
        "trusted",
        "version_display",
        "last_updated",
        "visit_link",
    )
    search_fields = (
        "hostname",
        "network_hostname",
        "address",
        "mac_address",
    )
    change_list_template = "admin/nodes/node/change_list.html"
    change_form_template = "admin/nodes/node/change_form.html"
    form = NodeAdminForm
    fieldsets = (
        (
            _("Network"),
            {
                "fields": (
                    "hostname",
                    "base_site",
                    "network_hostname",
                    "ipv4_address",
                    "ipv6_address",
                    "address",
                    "mac_address",
                    "port",
                    "message_queue_length",
                    "current_relation",
                    "trusted",
                )
            },
        ),
        (_("Role"), {"fields": ("role",)}),
        (
            _("Public endpoint"),
            {
                "fields": (
                    "public_endpoint",
                    "public_key",
                )
            },
        ),
        (
            _("Installation"),
            {
                "fields": (
                    "base_path",
                    "installed_version",
                    "installed_revision",
                )
            },
        ),
    )
    actions = [
        update_selected_nodes,
        register_visitor,
        run_task,
        take_screenshots,
        download_evcs_firmware,
        create_charge_point_forwarder,
        import_rfids_from_selected,
        export_rfids_to_selected,
        send_net_message,
    ]

    def _create_registration_user(self):
        UserModel = get_user_model()
        expires_at = timezone.now() + timedelta(hours=1)
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        for _ in range(5):
            username = f"node-register-{uuid.uuid4().hex[:12]}"
            if not manager.filter(username=username).exists():
                break
        user = manager.create(
            username=username,
            is_staff=True,
            is_superuser=False,
            temporary_expires_at=expires_at,
        )
        password = temp_passwords.generate_password()
        user.set_password(password)
        user.save()
        permissions = Permission.objects.filter(
            content_type__app_label=Node._meta.app_label,
            content_type__model=Node._meta.model_name,
            codename__in=["add_node", "change_node"],
        )
        user.user_permissions.set(permissions)
        return user, password, expires_at

    change_actions = ["update_node_action"]
    inlines = [NodeFeatureAssignmentInline, SSHAccountInline]

    @admin.display(description=_("Relation"), ordering="current_relation")
    def relation(self, obj):
        return obj.get_current_relation_display()

    @admin.display(description=_("MAC address"), ordering="mac_address")
    def mac_address_display(self, obj):
        return obj.mac_address or "—"

    @admin.display(description=_("Version"))
    def version_display(self, obj):
        display = _format_upgrade_body(
            getattr(obj, "installed_version", ""),
            getattr(obj, "installed_revision", ""),
        )
        return display or "—"

    @admin.display(description=_("IP Address"), ordering="address")
    def primary_ip(self, obj):
        if not obj:
            return ""
        return obj.get_best_ip() or ""

    @admin.display(description=_("Visit"))
    def visit_link(self, obj):
        if not obj:
            return ""
        if obj.is_local:
            try:
                url = reverse("admin:index")
            except NoReverseMatch:
                return ""
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                url,
                _("Visit"),
            )

        host_values = obj.get_remote_host_candidates(resolve_dns=False)

        remote_url = ""
        for host in host_values:
            temp_node = SimpleNamespace(
                public_endpoint=host,
                address="",
                hostname="",
                port=obj.port,
            )
            remote_url = next(self._iter_remote_urls(temp_node, "/admin/"), "")
            if remote_url:
                break

        if not remote_url:
            return ""

        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            remote_url,
            _("Visit"),
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-current/",
                self.admin_site.admin_view(self.register_current),
                name="nodes_node_register_current",
            ),
            path(
                "register-visitor/",
                self.admin_site.admin_view(self.register_visitor_view),
                name="nodes_node_register_visitor",
            ),
            path(
                "<int:node_id>/public-key/",
                self.admin_site.admin_view(self.public_key),
                name="nodes_node_public_key",
            ),
            path(
                "update-selected/progress/",
                self.admin_site.admin_view(self.update_selected_progress),
                name="nodes_node_update_selected_progress",
            ),
        ]
        return custom + urls

    def register_current(self, request):
        """Create or update this host and offer browser node registration."""
        if not request.user.is_superuser:
            raise PermissionDenied
        node, created = Node.register_current()
        if created:
            self.message_user(
                request, f"Current host registered as {node}", messages.SUCCESS
            )
        token = uuid.uuid4().hex
        context = {
            "token": token,
            "register_url": reverse("register-node"),
        }
        response = TemplateResponse(
            request, "admin/nodes/node/register_remote.html", context
        )
        response.render()
        template = response.resolve_template(response.template_name)
        if getattr(template, "name", None) in (None, ""):
            template.name = response.template_name
        signals.template_rendered.send(
            sender=template.__class__,
            template=template,
            context=response.context_data,
            request=request,
        )
        return response

    def _coerce_metadata_value(self, value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (bytes, bytearray)):
            return base64.b64encode(bytes(value)).decode("ascii")
        if isinstance(value, Mapping):
            return {k: self._coerce_metadata_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._coerce_metadata_value(v) for v in value]
        return str(value)

    def _decode_payload_bytes(self, value, encoding_hint: str = ""):
        if isinstance(value, (bytes, bytearray)):
            return bytes(value), encoding_hint or "binary"
        if not isinstance(value, str):
            return None, encoding_hint
        text = value.strip()
        if not text:
            return b"", encoding_hint or "binary"
        try:
            decoded = base64.b64decode(text, validate=True)
            return decoded, "base64"
        except (binascii.Error, ValueError):
            return None, encoding_hint

    def _extract_firmware_payload(self, data):
        content_type = "application/octet-stream"
        encoding = ""
        filename = ""
        json_payload = None
        binary_payload = None
        metadata: dict[str, object] = {}

        if isinstance(data, Mapping):
            metadata = {
                key: self._coerce_metadata_value(value)
                for key, value in data.items()
                if key not in {"payload", "data", "json"}
            }
            filename = str(data.get("filename") or data.get("name") or "").strip()
            if data.get("contentType"):
                content_type_candidate = str(data.get("contentType")).strip()
                if content_type_candidate:
                    content_type = content_type_candidate
            encoding = str(data.get("encoding") or "").strip()
            raw_payload = data.get("payload")
            if raw_payload is None:
                raw_payload = data.get("data")
            if raw_payload is not None:
                binary_payload, encoding = self._decode_payload_bytes(
                    raw_payload, encoding
                )
            json_candidate = data.get("json")
            if json_candidate is not None:
                if isinstance(json_candidate, str):
                    try:
                        json_payload = json.loads(json_candidate)
                    except json.JSONDecodeError:
                        metadata["json_raw"] = json_candidate
                else:
                    json_payload = json_candidate
            if json_payload is None and binary_payload is None:
                remaining = {
                    key: value
                    for key, value in data.items()
                    if key
                    not in {
                        "payload",
                        "data",
                        "encoding",
                        "contentType",
                        "filename",
                        "json",
                    }
                }
                if remaining:
                    json_payload = remaining
        elif isinstance(data, (bytes, bytearray)):
            binary_payload = bytes(data)
            encoding = encoding or "binary"
        elif isinstance(data, str):
            metadata = {"raw": data}
            binary_payload, encoding = self._decode_payload_bytes(data, encoding)
            if binary_payload is None:
                try:
                    json_payload = json.loads(data)
                except json.JSONDecodeError:
                    binary_payload = data.encode("utf-8")
                    encoding = encoding or "utf-8"
        elif data is not None:
            metadata = {"raw": self._coerce_metadata_value(data)}
            json_payload = metadata.get("raw")

        return {
            "binary": binary_payload,
            "json": json_payload,
            "encoding": encoding,
            "content_type": content_type,
            "filename": filename,
            "metadata": metadata,
        }

    def _format_pending_failure(self, action: str, result: Mapping) -> str:
        label_map = {
            "DataTransfer": _("Data transfer"),
            "UpdateFirmware": _("Update firmware"),
        }
        action_label = label_map.get(action, action)
        error_code = str(result.get("error_code") or "").strip()
        error_description = str(result.get("error_description") or "").strip()
        details = result.get("error_details")
        parts: list[str] = []
        if error_code:
            parts.append(_("code=%(code)s") % {"code": error_code})
        if error_description:
            parts.append(
                _("description=%(description)s")
                % {"description": error_description}
            )
        if details:
            try:
                details_text = json.dumps(
                    details, sort_keys=True, ensure_ascii=False
                )
            except TypeError:
                details_text = str(details)
            if details_text:
                parts.append(_("details=%(details)s") % {"details": details_text})
        if parts:
            return _("%(action)s failed: %(details)s") % {
                "action": action_label,
                "details": ", ".join(parts),
            }
        return _("%(action)s failed.") % {"action": action_label}

    def _process_firmware_download(self, request, node: Node, cleaned_data) -> bool:
        charger: Charger = cleaned_data["charger"]
        vendor_id = cleaned_data.get("vendor_id", "")
        pending_request = CPFirmwareRequest.objects.filter(
            charger=charger,
            responded_at__isnull=True,
        ).order_by("-requested_at").first()
        if pending_request:
            self.message_user(
                request,
                _(
                    "A firmware request for %(charger)s is still pending. "
                    "Wait for the existing request to finish before issuing "
                    "another."
                )
                % {"charger": charger},
                level=messages.ERROR,
            )
            return False
        connection = store.get_connection(charger.charger_id, charger.connector_id)
        if connection is None:
            self.message_user(
                request,
                _("%(charger)s is not currently connected to the platform.")
                % {"charger": charger},
                level=messages.ERROR,
            )
            return False

        message_id = uuid.uuid4().hex
        payload = {
            "vendorId": vendor_id,
            "messageId": "DownloadFirmware",
        }
        log_key = store.identity_key(charger.charger_id, charger.connector_id)
        message_record = DataTransferMessage.objects.create(
            charger=charger,
            connector_id=charger.connector_id,
            direction=DataTransferMessage.DIRECTION_CSMS_TO_CP,
            ocpp_message_id=message_id,
            vendor_id=vendor_id,
            message_id="DownloadFirmware",
            payload=payload,
            status="Pending",
        )
        CPFirmwareRequest.objects.create(
            charger=charger,
            connector_id=charger.connector_id,
            vendor_id=vendor_id,
            message=message_record,
        )

        frame = json.dumps([2, message_id, "DataTransfer", payload])
        async_to_sync(connection.send)(frame)
        store.add_log(
            log_key,
            _("Requested firmware download via DataTransfer."),
            log_type="charger",
        )
        store.register_pending_call(
            message_id,
            {
                "action": "DataTransfer",
                "charger_id": charger.charger_id,
                "connector_id": charger.connector_id,
                "log_key": log_key,
                "message_pk": message_record.pk,
            },
        )
        store.schedule_call_timeout(
            message_id, action="DataTransfer", log_key=log_key
        )

        result = store.wait_for_pending_call(message_id, timeout=15.0)
        if result is None:
            DataTransferMessage.objects.filter(pk=message_record.pk).update(
                status="Timeout"
            )
            CPFirmwareRequest.objects.filter(message=message_record).update(
                status="Timeout"
            )
            self.message_user(
                request,
                _("The charge point did not respond to the firmware request."),
                level=messages.ERROR,
            )
            return False
        if not result.get("success", True):
            detail = self._format_pending_failure("DataTransfer", result)
            CPFirmwareRequest.objects.filter(message=message_record).update(
                status="Error"
            )
            self.message_user(request, detail, level=messages.ERROR)
            return False

        payload_data = result.get("payload") or {}
        status_value = str(payload_data.get("status") or "").strip()
        if status_value.lower() != "accepted":
            self.message_user(
                request,
                _(
                    "Firmware request for %(charger)s was %(status)s."
                )
                % {"charger": charger, "status": status_value or "Rejected"},
                level=messages.ERROR,
            )
            return False

        data_section = payload_data.get("data")
        extracted = self._extract_firmware_payload(data_section)
        binary_payload = extracted["binary"]
        json_payload = extracted["json"]
        if binary_payload is None and json_payload is None:
            self.message_user(
                request,
                _("The charge point did not include a firmware payload."),
                level=messages.ERROR,
            )
            return False

        now = timezone.now()
        filename = extracted["filename"] or ""
        if not filename:
            suffix = ".bin" if binary_payload is not None else ".json"
            filename = f"{charger.charger_id}_{now:%Y%m%d%H%M%S}{suffix}"

        metadata = {
            "vendor_id": vendor_id,
            "response": self._coerce_metadata_value(payload_data),
        }
        metadata.update(extracted["metadata"])

        firmware = CPFirmware(
            name=f"{charger.charger_id} firmware {now:%Y-%m-%d %H:%M:%S}",
            source=CPFirmware.Source.DOWNLOAD,
            source_node=node,
            source_charger=charger,
            filename=filename,
            payload_binary=binary_payload,
            payload_json=json_payload,
            payload_encoding=extracted["encoding"],
            content_type=extracted["content_type"],
            metadata=metadata,
            download_vendor_id=vendor_id,
            download_message_id=message_id,
            downloaded_at=now,
            is_user_data=True,
        )
        firmware.save()

        self.message_user(
            request,
            _("Stored firmware from %(charger)s as %(firmware)s.")
            % {"charger": charger, "firmware": firmware},
            level=messages.SUCCESS,
        )
        return True

    def update_selected_progress(self, request):
        if request.method != "POST":
            return JsonResponse({"detail": "POST required"}, status=405)
        if not self.has_change_permission(request):
            raise PermissionDenied
        try:
            node_id = int(request.POST.get("node_id", ""))
        except (TypeError, ValueError):
            return JsonResponse({"detail": "Invalid node id"}, status=400)
        node = self.get_queryset(request).filter(pk=node_id).first()
        if not node:
            return JsonResponse({"detail": "Node not found"}, status=404)

        if node.current_relation == Node.Relation.DOWNSTREAM:
            message = _("Downstream Skipped")
            return JsonResponse(
                {
                    "node": str(node),
                    "status": "skipped",
                    "local": {"ok": True, "message": message},
                    "remote": {"ok": True, "message": message},
                }
            )

        local_result = self._refresh_local_information(node)
        remote_result = self._push_remote_information(node)

        status = "success"
        if not local_result.get("ok") and not remote_result.get("ok"):
            status = "error"
        elif not local_result.get("ok") or not remote_result.get("ok"):
            status = "partial"

        return JsonResponse(
            {
                "node": str(node),
                "status": status,
                "local": local_result,
                "remote": remote_result,
            }
        )

    def _refresh_local_information(self, node):
        if node.is_local:
            try:
                _, created = Node.register_current()
            except Exception as exc:  # pragma: no cover - unexpected errors
                return {"ok": False, "message": str(exc)}
            return {
                "ok": True,
                "created": created,
                "message": "Local node registration refreshed.",
            }

        last_error = ""
        host_candidates = node.get_remote_host_candidates()
        for url in self._iter_remote_urls(node, "/nodes/info/"):
            try:
                response = requests.get(url, timeout=5)
            except RequestException as exc:
                last_error = str(exc)
                continue
            if not response.ok:
                last_error = f"{response.status_code} {response.reason}"
                continue
            try:
                payload = response.json()
            except ValueError:
                last_error = "Invalid JSON response"
                continue
            updated = self._apply_remote_node_info(node, payload)
            message = (
                "Remote information applied."
                if updated
                else "Remote information fetched (no changes)."
            )
            return {
                "ok": True,
                "url": url,
                "updated_fields": updated,
                "message": message,
            }
        return {
            "ok": False,
            "message": self._build_connectivity_hint(last_error, host_candidates),
        }

    def _apply_remote_node_info(self, node, payload):
        changed: list[str] = []
        sentinel = object()

        def payload_value(key):
            return payload[key] if key in payload else sentinel

        def apply_field(field: str, value):
            if value is sentinel:
                return
            field_obj = node._meta.get_field(field)
            current = getattr(node, field)
            if isinstance(field_obj, (models.CharField, models.TextField, models.SlugField)):
                value = value or ""
                current = current or ""
            if current != value:
                setattr(node, field, value)
                changed.append(field)

        apply_field("hostname", payload_value("hostname"))
        apply_field("network_hostname", payload_value("network_hostname"))
        apply_field("address", payload_value("address"))

        ipv4_raw = payload_value("ipv4_address")
        if ipv4_raw is not sentinel:
            ipv4_value = Node.serialize_ipv4_addresses(ipv4_raw) or ""
            apply_field("ipv4_address", ipv4_value)

        apply_field("ipv6_address", payload_value("ipv6_address"))
        apply_field("public_key", payload_value("public_key"))

        port_value = payload_value("port")
        if port_value is not sentinel:
            try:
                port_value = int(port_value)
            except (TypeError, ValueError):
                port_value = sentinel
        apply_field("port", port_value)

        mac_address = payload_value("mac_address")
        if mac_address is not sentinel and mac_address:
            apply_field("mac_address", str(mac_address).lower())

        version_value = payload_value("installed_version")
        if version_value is not sentinel:
            cleaned_version = "" if version_value is None else str(version_value)[:20]
            apply_field("installed_version", cleaned_version)

        revision_value = payload_value("installed_revision")
        if revision_value is not sentinel:
            cleaned_revision = "" if revision_value is None else str(revision_value)[:40]
            apply_field("installed_revision", cleaned_revision)

        role_value = payload.get("role") or payload.get("role_name")
        if role_value is not None:
            role_name = str(role_value).strip()
            if role_name:
                desired_role = NodeRole.objects.filter(name=role_name).first()
            else:
                desired_role = None
            if desired_role and node.role_id != desired_role.id:
                node.role = desired_role
                changed.append("role")

        node.last_updated = timezone.now()
        if "last_updated" not in changed:
            changed.append("last_updated")
        node.save(update_fields=changed)
        return changed

    def _push_remote_information(self, node):
        if node.is_local:
            return {
                "ok": True,
                "message": "Local node does not require remote update.",
            }

        local_node = Node.get_local()
        if local_node is None:
            try:
                local_node, _ = Node.register_current()
            except Exception as exc:  # pragma: no cover - unexpected errors
                return {"ok": False, "message": str(exc)}

        security_dir = local_node.get_base_path() / "security"
        priv_path = security_dir / f"{local_node.public_endpoint}"
        if not priv_path.exists():
            return {
                "ok": False,
                "message": "Local node private key not found.",
            }
        try:
            private_key = serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
        except Exception as exc:  # pragma: no cover - unexpected errors
            return {"ok": False, "message": f"Failed to load private key: {exc}"}

        token = uuid.uuid4().hex
        signature, error = Node.sign_payload(token, private_key)
        if error or not signature:
            return {"ok": False, "message": f"Failed to sign payload: {error}"}

        payload = {
            "hostname": local_node.hostname,
            "network_hostname": local_node.network_hostname,
            "address": local_node.address,
            "ipv4_address": local_node.ipv4_address,
            "ipv6_address": local_node.ipv6_address,
            "port": local_node.port,
            "mac_address": local_node.mac_address,
            "public_key": local_node.public_key,
            "token": token,
            "signature": signature,
        }
        if local_node.installed_version:
            payload["installed_version"] = local_node.installed_version
        if local_node.installed_revision:
            payload["installed_revision"] = local_node.installed_revision

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}

        last_error = ""
        host_candidates = node.get_remote_host_candidates()
        for url in self._iter_remote_urls(node, "/nodes/register/"):
            try:
                response = requests.post(
                    url,
                    data=payload_json,
                    headers=headers,
                    timeout=5,
                )
            except RequestException as exc:
                last_error = str(exc)
                continue
            if response.ok:
                return {"ok": True, "url": url, "message": "Remote updated."}
            last_error = f"{response.status_code} {response.text}"
        return {
            "ok": False,
            "message": self._build_connectivity_hint(last_error, host_candidates),
        }

    def _build_connectivity_hint(self, last_error: str, hosts: list[str]) -> str:
        base_message = last_error or _("Unable to reach remote node.")
        if hosts:
            host_text = ", ".join(hosts)
            return _("%(message)s Tried hosts: %(hosts)s.") % {
                "message": base_message,
                "hosts": host_text,
            }
        return _("%(message)s No remote hosts were available for contact.") % {
            "message": base_message
        }

    def _primary_remote_url(self, node, path: str) -> str:
        return next(self._iter_remote_urls(node, path), "")

    def _request_remote(self, node, path: str, request_callable):
        errors: list[str] = []
        for url in self._iter_remote_urls(node, path):
            try:
                response = request_callable(url)
            except RequestException as exc:
                errors.append(f"{url}: {exc}")
                continue
            return url, response, errors
        return "", None, errors

    def _iter_remote_urls(self, node, path):
        if hasattr(node, "iter_remote_urls"):
            yield from node.iter_remote_urls(path)
            return

        temp = Node(
            public_endpoint=getattr(node, "public_endpoint", ""),
            address=getattr(node, "address", ""),
            hostname=getattr(node, "hostname", ""),
            port=getattr(node, "port", None),
        )
        temp.network_hostname = getattr(node, "network_hostname", "")
        temp.ipv4_address = getattr(node, "ipv4_address", "")
        temp.ipv6_address = getattr(node, "ipv6_address", "")
        yield from temp.iter_remote_urls(path)

    def _detect_visitor_host(self, request) -> tuple[str | None, int | None]:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
        candidates = [value.strip() for value in forwarded_for.split(",") if value.strip()]
        remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
        if remote_addr:
            candidates.append(remote_addr)

        for candidate in candidates:
            host = candidate
            port: int | None = None

            if ":" in candidate and candidate.count(":") == 1:
                host_part, port_part = candidate.rsplit(":", 1)
                if port_part.isdigit():
                    host = host_part
                    try:
                        port = int(port_part)
                    except (TypeError, ValueError):
                        port = None

            try:
                ip_obj = ipaddress.ip_address(host.strip("[]"))
                host = str(ip_obj)
                if ip_obj.version == 6 and not host.startswith("["):
                    host = f"[{host}]"
            except ValueError:
                host = host.strip()

            if host:
                return host, port

        return None, None

    def _resolve_visitor_base(self, request, default_port: int = 443):
        raw_port = default_port
        raw = "127.0.0.1"

        candidate = raw
        if "://" not in candidate:
            candidate = f"//{candidate.lstrip('/')}"

        parsed = urlsplit(candidate)
        hostname = parsed.hostname or ""
        if not hostname:
            return None, "", default_port, "https"

        scheme = (parsed.scheme or "https").lower()
        if scheme != "https":
            scheme = "https"

        port = parsed.port or raw_port or default_port
        if ":" in hostname and not hostname.startswith("["):
            host_part = f"[{hostname}]"
        else:
            host_part = hostname
        if port:
            host_part = f"{host_part}:{port}"

        return urlunsplit((scheme, host_part, "", "", "")), hostname, port, scheme

    def register_visitor_view(self, request):
        """Exchange registration data with the visiting node."""

        node, created = Node.register_current()
        registration_logger.info(
            "Visitor registration: ensuring local node registration user=%s created=%s node=%s",
            getattr(request.user, "username", None) or str(request.user),
            created,
            node,
        )
        if created:
            self.message_user(
                request, f"Current host registered as {node}", messages.SUCCESS
            )

        token = uuid.uuid4().hex
        visitor_base, visitor_host, visitor_port, visitor_scheme = self._resolve_visitor_base(request)
        visitor_info_url = ""
        visitor_register_url = ""
        visitor_error = None
        if visitor_base:
            visitor_base = visitor_base.rstrip("/")
            visitor_info_url = f"{visitor_base}/nodes/info/"
            visitor_register_url = f"{visitor_base}/nodes/register/"
        else:
            visitor_error = _(
                "Visitor address missing or invalid. Append a ?visitor=host[:port] query string to continue."
            )
        registration_logger.info(
            "Visitor registration: admin flow initialized visitor_base=%s token=%s",
            visitor_base or "",
            token,
        )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Register Visitor"),
            "token": token,
            "info_url": reverse("node-info"),
            "register_url": reverse("register-node"),
            "telemetry_url": reverse("register-telemetry"),
            "visitor_proxy_url": reverse("register-visitor-proxy"),
            "visitor_info_url": visitor_info_url,
            "visitor_register_url": visitor_register_url,
            "visitor_error": visitor_error,
            "visitor_host": visitor_host,
            "visitor_port": visitor_port,
            "visitor_scheme": visitor_scheme,
            "local_node": {
                "hostname": node.get_preferred_hostname(),
                "address": node.get_base_domain() or node.address or node.network_hostname,
                "port": node._preferred_site_port(True)
                if node.get_base_domain()
                else node.port or node.get_preferred_port(),
            },
            "change_url_template": reverse("admin:nodes_node_change", args=[0]),
        }
        return render(request, "admin/nodes/node/register_visitor.html", context)

    def public_key(self, request, node_id):
        node = self.get_object(request, node_id)
        if not node:
            self.message_user(request, "Unknown node", messages.ERROR)
            return redirect("..")
        security_dir = local_node.get_base_path() / "security"
        pub_path = security_dir / f"{node.public_endpoint}.pub"
        if pub_path.exists():
            response = HttpResponse(pub_path.read_bytes(), content_type="text/plain")
            response["Content-Disposition"] = f'attachment; filename="{pub_path.name}"'
            return response
        self.message_user(request, "Public key not found", messages.ERROR)
        return redirect("..")

    def _init_rfid_result(self, node):
        return {
            "node": node,
            "status": "success",
            "created": 0,
            "updated": 0,
            "linked_accounts": 0,
            "missing_accounts": [],
            "errors": [],
            "processed": 0,
            "message": None,
        }

    def _skip_result(self, node, message):
        result = self._init_rfid_result(node)
        result["status"] = "skipped"
        result["message"] = message
        return result

    def _load_local_node_credentials(self):
        local_node = Node.get_local()
        if not local_node:
            return None, None, _("Local node is not registered.")

        endpoint = (local_node.public_endpoint or "").strip()
        if not endpoint:
            return local_node, None, _(
                "Local node public endpoint is not configured."
            )

        security_dir = local_node.get_base_path() / "security"
        priv_path = security_dir / endpoint
        if not priv_path.exists():
            return local_node, None, _("Local node private key not found.")

        try:
            private_key = serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
        except Exception as exc:  # pragma: no cover - unexpected key errors
            return local_node, None, _("Failed to load private key: %(error)s") % {
                "error": exc
            }

        return local_node, private_key, None

    def _send_forwarding_metadata(
        self,
        request,
        target,
        chargers,
        local_node,
        private_key,
    ) -> bool:
        chargers = list(chargers)

        def _safe_message(level: int, text: str) -> None:
            if hasattr(request, "_messages"):
                self.message_user(request, text, level=level)

        if not chargers:
            _safe_message(messages.INFO, _("No chargers available to forward."))
            return True

        payload = {
            "requester": str(local_node.uuid),
            "requester_mac": local_node.mac_address,
            "requester_public_key": local_node.public_key,
            "chargers": [
                serialize_charger_for_network(charger) for charger in chargers
            ],
            "transactions": {"chargers": [], "transactions": []},
        }
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}
        if private_key:
            signature, error = Node.sign_payload(payload_json, private_key)
            if error or not signature:
                _safe_message(
                    messages.ERROR,
                    _("Failed to sign forwarding payload: %(error)s")
                    % {"error": error},
                )
                return False
            headers["X-Signature"] = signature

        errors: list[str] = []
        for url in self._iter_remote_urls(target, "/nodes/network/chargers/forward/"):
            if not url:
                continue
            try:
                response = requests.post(
                    url,
                    data=payload_json,
                    headers=headers,
                    timeout=5,
                )
            except RequestException as exc:
                errors.append(
                    _(
                        "Failed to send forwarding metadata to %(node)s via %(url)s (%(error)s)."
                    )
                    % {"node": target, "url": url, "error": exc}
                )
                continue

            try:
                data: Mapping = response.json()
            except ValueError:
                data = {}

            if response.ok and isinstance(data, Mapping) and data.get("status") == "ok":
                _safe_message(
                    messages.SUCCESS,
                    _("Forwarding metadata sent to %(node)s via %(url)s.")
                    % {"node": target, "url": url},
                )
                return True

            detail = ""
            if isinstance(data, Mapping):
                detail = data.get("detail") or ""
            errors.append(
                _(
                    "Forwarding metadata to %(node)s via %(url)s failed: %(status)s %(detail)s"
                )
                % {
                    "node": target,
                    "url": url,
                    "status": response.status_code,
                    "detail": detail,
                }
            )

        if errors:
            for message in errors:
                _safe_message(messages.ERROR, message)
        else:
            _safe_message(
                messages.ERROR,
                _("No reachable host found for %(node)s.") % {"node": target},
            )
        return False

    def _dedupe(self, values):
        if not values:
            return []
        return list(OrderedDict.fromkeys(values))

    def _status_from_result(self, result):
        if result["errors"]:
            return "error"
        if result["missing_accounts"]:
            return "partial"
        return result.get("status") or "success"

    def _summarize_rfid_results(self, results):
        return {
            "total": len(results),
            "processed": sum(1 for item in results if item["status"] != "skipped"),
            "success": sum(1 for item in results if item["status"] == "success"),
            "partial": sum(1 for item in results if item["status"] == "partial"),
            "error": sum(1 for item in results if item["status"] == "error"),
            "created": sum(item["created"] for item in results),
            "updated": sum(item["updated"] for item in results),
            "linked_accounts": sum(item["linked_accounts"] for item in results),
            "missing_accounts": sum(
                len(item["missing_accounts"]) for item in results
            ),
        }

    def _render_rfid_sync(self, request, operation, results, setup_error=None):
        titles = {
            "import": _("Import RFID results"),
            "fetch": _("Fetch RFID results"),
            "export": _("Export RFID results"),
        }
        summary = self._summarize_rfid_results(results)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": titles.get(operation, _("RFID results")),
            "operation": operation,
            "results": results,
            "summary": summary,
            "setup_error": setup_error,
            "back_url": reverse("admin:nodes_node_changelist"),
        }
        return TemplateResponse(
            request,
            "admin/cards/rfid_sync_results.html",
            context,
        )

    def _process_import_from_node(self, node, payload, headers):
        result = self._init_rfid_result(node)
        _, response, attempt_errors = self._request_remote(
            node,
            "/rfid/export/",
            lambda url: requests.post(url, data=payload, headers=headers, timeout=5),
        )
        if response is None:
            result["status"] = "error"
            if attempt_errors:
                result["errors"].extend(attempt_errors)
            else:
                result["errors"].append(
                    _("No remote hosts were available for %(node)s.") % {"node": node}
                )
            return result

        if response.status_code != 200:
            result["status"] = "error"
            result["errors"].append(f"{response.status_code} {response.text}")
            return result

        try:
            data = response.json()
        except ValueError:
            result["status"] = "error"
            result["errors"].append(_("Invalid JSON response"))
            return result

        rfids = data.get("rfids", []) or []
        result["processed"] = len(rfids)
        for entry in rfids:
            if not isinstance(entry, Mapping):
                result["errors"].append(_( "Invalid RFID payload" ))
                continue
            outcome = apply_rfid_payload(entry, origin_node=node)
            if not outcome.ok:
                result["errors"].append(
                    outcome.error or _("RFID could not be imported")
                )
                continue
            if outcome.created:
                result["created"] += 1
            else:
                result["updated"] += 1
            result["linked_accounts"] += outcome.accounts_linked
            result["missing_accounts"].extend(outcome.missing_accounts)

        result["missing_accounts"] = self._dedupe(result["missing_accounts"])
        result["status"] = self._status_from_result(result)
        return result

    def _post_export_to_node(self, node, payload, headers):
        result = self._init_rfid_result(node)
        _, response, attempt_errors = self._request_remote(
            node,
            "/rfid/import/",
            lambda url: requests.post(url, data=payload, headers=headers, timeout=5),
        )
        if response is None:
            result["status"] = "error"
            if attempt_errors:
                result["errors"].extend(attempt_errors)
            else:
                result["errors"].append(
                    _("No remote hosts were available for %(node)s.") % {"node": node}
                )
            return result

        if response.status_code != 200:
            result["status"] = "error"
            result["errors"].append(f"{response.status_code} {response.text}")
            return result

        try:
            data = response.json()
        except ValueError:
            result["status"] = "error"
            result["errors"].append(_("Invalid JSON response"))
            return result

        result["processed"] = data.get("processed", 0) or 0
        result["created"] = data.get("created", 0) or 0
        result["updated"] = data.get("updated", 0) or 0
        result["linked_accounts"] = data.get("accounts_linked", 0) or 0

        missing = data.get("missing_accounts") or []
        if isinstance(missing, list):
            result["missing_accounts"].extend(str(value) for value in missing if value)
        elif missing:
            result["missing_accounts"].append(str(missing))

        errors = data.get("errors", 0)
        if isinstance(errors, int) and errors:
            result["errors"].append(
                _("Remote reported %(count)s error(s).") % {"count": errors}
            )
        elif isinstance(errors, list):
            result["errors"].extend(str(err) for err in errors if err)

        result["missing_accounts"] = self._dedupe(result["missing_accounts"])
        result["status"] = self._status_from_result(result)
        return result

    def _run_rfid_import(self, request, queryset):
        nodes = list(queryset)
        local_node, private_key, error = self._load_local_node_credentials()
        if error:
            results = [self._skip_result(node, error) for node in nodes]
            return self._render_rfid_sync(
                request, "import", results, setup_error=error
            )

        if not nodes:
            return self._render_rfid_sync(
                request,
                "import",
                [],
                setup_error=_("No nodes selected."),
            )

        payload = json.dumps(
            {"requester": str(local_node.uuid)},
            separators=(",", ":"),
            sort_keys=True,
        )
        signature, error = Node.sign_payload(payload, private_key)
        if error or not signature:
            message = _("Failed to sign payload.")
            if error:
                message = _("Failed to sign payload: %(error)s") % {"error": error}
            return self._render_rfid_sync(
                request, "import", [], setup_error=message
            )
        headers = {
            "Content-Type": "application/json",
            "X-Signature": signature,
        }

        results = []
        for node in nodes:
            if local_node.pk and node.pk == local_node.pk:
                results.append(self._skip_result(node, _("Skipped local node.")))
                continue
            results.append(self._process_import_from_node(node, payload, headers))

        return self._render_rfid_sync(request, "import", results)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["public_key_url"] = reverse(
                "admin:nodes_node_public_key", args=[object_id]
            )
        return super().changeform_view(
            request, object_id, form_url, extra_context=extra_context
        )

    def get_queryset(self, request):
        return super().get_queryset(request)

    def _format_update_detail(self, label: str, result: Mapping[str, object]) -> str:
        ok = bool(result.get("ok"))
        status = _("succeeded") if ok else _("failed")
        message = str(result.get("message") or "").strip()
        url = str(result.get("url") or "").strip()
        detail_parts: list[str] = []
        if message:
            detail_parts.append(message)
        if url:
            detail_parts.append(_("URL: %(url)s") % {"url": url})
        if detail_parts:
            return _("%(label)s %(status)s: %(detail)s") % {
                "label": label,
                "status": status,
                "detail": " ".join(detail_parts),
            }
        return _("%(label)s %(status)s.") % {"label": label, "status": status}

    def update_node_action(self, request, obj):
        local_result = self._refresh_local_information(obj)
        remote_result = self._push_remote_information(obj)

        local_ok = bool(local_result.get("ok"))
        remote_ok = bool(remote_result.get("ok"))
        if local_ok and remote_ok:
            status_key = "success"
        elif local_ok or remote_ok:
            status_key = "partial"
        else:
            status_key = "error"

        status_messages = {
            "success": _("Update succeeded for %(node)s."),
            "partial": _("Update partially succeeded for %(node)s."),
            "error": _("Update failed for %(node)s."),
        }
        level_map = {
            "success": messages.SUCCESS,
            "partial": messages.WARNING,
            "error": messages.ERROR,
        }
        details = [
            self._format_update_detail(_("Local"), local_result),
            self._format_update_detail(_("Remote"), remote_result),
        ]
        detail_text = " ".join(filter(None, details))
        message = status_messages[status_key] % {"node": obj}
        if detail_text:
            message = f"{message} {detail_text}"

        if getattr(obj, "pk", None):
            obj.refresh_from_db()

        self.message_user(request, message, level=level_map[status_key])

    update_node_action.label = _("Update Node")
    update_node_action.short_description = _("Update Node")
