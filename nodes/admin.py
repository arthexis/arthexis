from collections import OrderedDict
from collections.abc import Mapping

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import base64
import json
import subprocess
import uuid

import pyperclip
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pyperclip import PyperclipException
from requests import RequestException

from .classifiers import run_default_classifiers, suppress_default_classifiers
from .rfid_sync import apply_rfid_payload, serialize_rfid
from .utils import capture_rpi_snapshot, capture_screenshot, save_screenshot
from .reports import (
    collect_celery_log_entries,
    collect_scheduled_tasks,
    iter_report_periods,
    resolve_period,
)

from core.admin import EmailOutboxAdminForm
from .models import (
    Node,
    EmailOutbox,
    NodeRole,
    NodeFeature,
    NodeFeatureAssignment,
    ContentSample,
    ContentClassifier,
    ContentClassification,
    ContentTag,
    NetMessage,
    NodeManager,
    DNSRecord,
)
from . import dns as dns_utils
from core.models import RFID
from core.user_data import EntityModelAdmin


class NodeAdminForm(forms.ModelForm):
    class Meta:
        model = Node
        exclude = ("badge_color", "features")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        enable_public = self.fields.get("enable_public_api")
        if enable_public:
            enable_public.label = _("Enable public admin access")
            enable_public.help_text = _(
                "Expose the admin API through this node's public endpoint. "
                "Only enable when trusted peers require administrative access."
            )


class NodeFeatureAssignmentInline(admin.TabularInline):
    model = NodeFeatureAssignment
    extra = 0
    autocomplete_fields = ("feature",)


class DeployDNSRecordsForm(forms.Form):
    manager = forms.ModelChoiceField(
        label="Node Manager",
        queryset=NodeManager.objects.none(),
        help_text="Credentials used to authenticate with the DNS provider.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = NodeManager.objects.filter(
            provider=NodeManager.Provider.GODADDY, is_enabled=True
        )


@admin.register(NodeManager)
class NodeManagerAdmin(EntityModelAdmin):
    list_display = ("__str__", "provider", "is_enabled", "default_domain")
    list_filter = ("provider", "is_enabled")
    search_fields = (
        "default_domain",
        "user__username",
        "group__name",
    )
    fieldsets = (
        (_("Owner"), {"fields": ("user", "group")}),
        (
            _("Credentials"),
            {"fields": ("api_key", "api_secret", "customer_id")},
        ),
        (
            _("Configuration"),
            {
                "fields": (
                    "provider",
                    "default_domain",
                    "use_sandbox",
                    "is_enabled",
                )
            },
        ),
    )


@admin.register(DNSRecord)
class DNSRecordAdmin(EntityModelAdmin):
    list_display = (
        "record_type",
        "fqdn",
        "data",
        "ttl",
        "node_manager",
        "last_synced_at",
        "last_verified_at",
    )
    list_filter = ("record_type", "provider", "node_manager")
    search_fields = ("domain", "name", "data")
    autocomplete_fields = ("node_manager",)
    actions = ["deploy_selected_records", "validate_selected_records"]

    def _default_manager_for_queryset(self, queryset):
        manager_ids = list(
            queryset.exclude(node_manager__isnull=True)
            .values_list("node_manager_id", flat=True)
            .distinct()
        )
        if len(manager_ids) == 1:
            return manager_ids[0]
        available = list(
            NodeManager.objects.filter(
                provider=NodeManager.Provider.GODADDY, is_enabled=True
            ).values_list("pk", flat=True)
        )
        if len(available) == 1:
            return available[0]
        return None

    @admin.action(description="Deploy Selected records")
    def deploy_selected_records(self, request, queryset):
        unsupported = queryset.exclude(provider=DNSRecord.Provider.GODADDY)
        for record in unsupported:
            self.message_user(
                request,
                f"{record} uses unsupported provider {record.get_provider_display()}",
                messages.WARNING,
            )
        queryset = queryset.filter(provider=DNSRecord.Provider.GODADDY)
        if not queryset:
            self.message_user(request, "No GoDaddy records selected.", messages.WARNING)
            return None

        if "apply" in request.POST:
            form = DeployDNSRecordsForm(request.POST)
            if form.is_valid():
                manager = form.cleaned_data["manager"]
                result = manager.publish_dns_records(list(queryset))
                for record, reason in result.skipped.items():
                    self.message_user(request, f"{record}: {reason}", messages.WARNING)
                for record, reason in result.failures.items():
                    self.message_user(request, f"{record}: {reason}", messages.ERROR)
                if result.deployed:
                    self.message_user(
                        request,
                        f"Deployed {len(result.deployed)} DNS record(s) via {manager}.",
                        messages.SUCCESS,
                    )
                return None
        else:
            initial_manager = self._default_manager_for_queryset(queryset)
            form = DeployDNSRecordsForm(initial={"manager": initial_manager})

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "queryset": queryset,
            "title": "Deploy DNS records",
        }
        return render(
            request,
            "admin/nodes/dnsrecord/deploy_records.html",
            context,
        )

    @admin.action(description="Validate Selected records")
    def validate_selected_records(self, request, queryset):
        resolver = dns_utils.create_resolver()
        successes = 0
        for record in queryset:
            ok, message = dns_utils.validate_record(record, resolver=resolver)
            if ok:
                successes += 1
            else:
                self.message_user(request, f"{record}: {message}", messages.WARNING)
        if successes:
            self.message_user(
                request,
                f"Validated {successes} DNS record(s).",
                messages.SUCCESS,
            )


@admin.register(Node)
class NodeAdmin(EntityModelAdmin):
    list_display = (
        "hostname",
        "mac_address",
        "address",
        "port",
        "role",
        "relation",
        "last_seen",
        "proxy_link",
    )
    search_fields = ("hostname", "address", "mac_address")
    change_list_template = "admin/nodes/node/change_list.html"
    change_form_template = "admin/nodes/node/change_form.html"
    form = NodeAdminForm
    fieldsets = (
        (
            _("Node"),
            {
                "fields": (
                    "hostname",
                    "address",
                    "mac_address",
                    "port",
                    "message_queue_length",
                    "role",
                    "current_relation",
                )
            },
        ),
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
        (
            _("Public admin"),
            {"fields": ("enable_public_api",)},
        ),
    )
    actions = [
        "update_selected_nodes",
        "register_visitor",
        "run_task",
        "take_screenshots",
        "import_rfids_from_selected",
        "export_rfids_to_selected",
    ]
    inlines = [NodeFeatureAssignmentInline]

    @admin.display(description=_("Relation"), ordering="current_relation")
    def relation(self, obj):
        return obj.get_current_relation_display()

    @admin.display(description=_("Proxy"))
    def proxy_link(self, obj):
        if not obj or obj.is_local:
            return ""
        try:
            url = reverse("admin:nodes_node_proxy", args=[obj.pk])
        except NoReverseMatch:
            return ""
        return format_html('<a class="button" href="{}">{}</a>', url, _("Proxy"))

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
            path(
                "<int:node_id>/proxy/",
                self.admin_site.admin_view(self.proxy_node),
                name="nodes_node_proxy",
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
        return render(request, "admin/nodes/node/register_remote.html", context)

    def _load_local_private_key(self, node):
        security_dir = Path(node.base_path or settings.BASE_DIR) / "security"
        priv_path = security_dir / f"{node.public_endpoint}"
        if not priv_path.exists():
            return None, _("Local node private key not found.")
        try:
            return (
                serialization.load_pem_private_key(
                    priv_path.read_bytes(), password=None
                ),
                "",
            )
        except Exception as exc:  # pragma: no cover - unexpected errors
            return None, str(exc)

    def _build_proxy_payload(self, request, local_node):
        user = request.user
        payload = {
            "requester": str(local_node.uuid),
            "user": {
                "username": user.get_username(),
                "email": user.email or "",
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "groups": list(user.groups.values_list("name", flat=True)),
                "permissions": sorted(user.get_all_permissions()),
            },
            "target": reverse("admin:index"),
        }
        return payload

    def _start_proxy_session(self, request, node):
        if node.is_local:
            return {"ok": False, "message": _("Local node cannot be proxied.")}

        local_node = Node.get_local()
        if local_node is None:
            try:
                local_node, _ = Node.register_current()
            except Exception as exc:  # pragma: no cover - unexpected errors
                return {"ok": False, "message": str(exc)}

        private_key, error = self._load_local_private_key(local_node)
        if private_key is None:
            return {"ok": False, "message": error}

        payload = self._build_proxy_payload(request, local_node)
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        try:
            signature = private_key.sign(
                body.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as exc:  # pragma: no cover - unexpected errors
            return {"ok": False, "message": str(exc)}

        headers = {
            "Content-Type": "application/json",
            "X-Signature": base64.b64encode(signature).decode(),
        }

        last_error = ""
        for url in self._iter_remote_urls(node, "/nodes/proxy/session/"):
            try:
                response = requests.post(url, data=body, headers=headers, timeout=5)
            except RequestException as exc:
                last_error = str(exc)
                continue
            if not response.ok:
                last_error = f"{response.status_code} {response.text}"
                continue
            try:
                data = response.json()
            except ValueError:
                last_error = "Invalid JSON response"
                continue
            login_url = data.get("login_url")
            if not login_url:
                last_error = "login_url missing"
                continue
            return {
                "ok": True,
                "login_url": login_url,
                "expires": data.get("expires"),
            }

        return {
            "ok": False,
            "message": last_error or "Unable to initiate proxy.",
        }

    def proxy_node(self, request, node_id):
        node = self.get_queryset(request).filter(pk=node_id).first()
        if not node:
            raise Http404
        if not self.has_view_permission(request):
            raise PermissionDenied
        result = self._start_proxy_session(request, node)
        if not result.get("ok"):
            message = result.get("message") or _("Unable to proxy node.")
            self.message_user(request, message, messages.ERROR)
            return redirect("admin:nodes_node_changelist")

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "node": node,
            "frame_url": result.get("login_url"),
            "expires": result.get("expires"),
        }
        return TemplateResponse(request, "admin/nodes/node/proxy.html", context)

    @admin.action(description="Register Visitor")
    def register_visitor(self, request, queryset=None):
        return self.register_visitor_view(request)

    @admin.action(description=_("Update selected nodes"))
    def update_selected_nodes(self, request, queryset):
        node_ids = list(queryset.values_list("pk", flat=True))
        if not node_ids:
            self.message_user(request, _("No nodes selected."), messages.INFO)
            return None
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Update selected nodes"),
            "nodes": list(queryset),
            "node_ids": node_ids,
            "progress_url": reverse("admin:nodes_node_update_selected_progress"),
        }
        return TemplateResponse(
            request, "admin/nodes/node/update_selected.html", context
        )

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
        return {"ok": False, "message": last_error or "Unable to reach remote node."}

    def _apply_remote_node_info(self, node, payload):
        changed = []
        field_map = {
            "hostname": payload.get("hostname"),
            "address": payload.get("address"),
            "public_key": payload.get("public_key"),
        }
        port_value = payload.get("port")
        if port_value is not None:
            try:
                port_value = int(port_value)
            except (TypeError, ValueError):
                port_value = None
        field_map["port"] = port_value
        mac_address = payload.get("mac_address")
        if mac_address:
            field_map["mac_address"] = str(mac_address).lower()

        for field, value in field_map.items():
            if value is None:
                continue
            if getattr(node, field) != value:
                setattr(node, field, value)
                changed.append(field)

        node.last_seen = timezone.now()
        if "last_seen" not in changed:
            changed.append("last_seen")
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

        security_dir = Path(local_node.base_path or settings.BASE_DIR) / "security"
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
        try:
            signature = private_key.sign(
                token.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as exc:  # pragma: no cover - unexpected errors
            return {"ok": False, "message": f"Failed to sign payload: {exc}"}

        payload = {
            "hostname": local_node.hostname,
            "address": local_node.address,
            "port": local_node.port,
            "mac_address": local_node.mac_address,
            "public_key": local_node.public_key,
            "token": token,
            "signature": base64.b64encode(signature).decode(),
        }
        if local_node.installed_version:
            payload["installed_version"] = local_node.installed_version
        if local_node.installed_revision:
            payload["installed_revision"] = local_node.installed_revision

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}

        last_error = ""
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
        return {"ok": False, "message": last_error or "Unable to reach remote node."}

    def _iter_remote_urls(self, node, path):
        host_candidates = []
        for attr in ("public_endpoint", "address", "hostname"):
            value = getattr(node, attr, "") or ""
            value = value.strip()
            if value and value not in host_candidates:
                host_candidates.append(value)

        port = node.port or 8000
        normalized_path = path if path.startswith("/") else f"/{path}"
        seen = set()

        for host in host_candidates:
            formatted_host = host
            if ":" in host and not host.startswith("["):
                formatted_host = f"[{host}]"

            candidates = []
            if port == 80:
                candidates = [
                    f"http://{formatted_host}{normalized_path}",
                    f"https://{formatted_host}{normalized_path}",
                ]
            elif port == 443:
                candidates = [
                    f"https://{formatted_host}{normalized_path}",
                    f"http://{formatted_host}:{port}{normalized_path}",
                ]
            else:
                candidates = [
                    f"http://{formatted_host}:{port}{normalized_path}",
                    f"https://{formatted_host}:{port}{normalized_path}",
                ]

            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    yield candidate

    def register_visitor_view(self, request):
        """Exchange registration data with the visiting node."""

        node, created = Node.register_current()
        if created:
            self.message_user(
                request, f"Current host registered as {node}", messages.SUCCESS
            )

        token = uuid.uuid4().hex
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Register Visitor"),
            "token": token,
            "info_url": reverse("node-info"),
            "register_url": reverse("register-node"),
            "visitor_info_url": "http://localhost:8000/nodes/info/",
            "visitor_register_url": "http://localhost:8000/nodes/register/",
        }
        return render(request, "admin/nodes/node/register_visitor.html", context)

    def public_key(self, request, node_id):
        node = self.get_object(request, node_id)
        if not node:
            self.message_user(request, "Unknown node", messages.ERROR)
            return redirect("..")
        security_dir = Path(settings.BASE_DIR) / "security"
        pub_path = security_dir / f"{node.public_endpoint}.pub"
        if pub_path.exists():
            response = HttpResponse(pub_path.read_bytes(), content_type="text/plain")
            response["Content-Disposition"] = f'attachment; filename="{pub_path.name}"'
            return response
        self.message_user(request, "Public key not found", messages.ERROR)
        return redirect("..")

    def run_task(self, request, queryset):
        if "apply" in request.POST:
            recipe_text = request.POST.get("recipe", "")
            results = []
            for node in queryset:
                try:
                    if not node.is_local:
                        raise NotImplementedError(
                            "Remote node execution is not implemented"
                        )
                    command = ["/bin/sh", "-c", recipe_text]
                    result = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    output = result.stdout + result.stderr
                except Exception as exc:
                    output = str(exc)
                results.append((node, output))
            context = {"recipe": recipe_text, "results": results}
            return render(request, "admin/nodes/task_result.html", context)
        context = {"nodes": queryset}
        return render(request, "admin/nodes/node/run_task.html", context)

    run_task.short_description = "Run task"

    @admin.action(description="Take Screenshots")
    def take_screenshots(self, request, queryset):
        tx = uuid.uuid4()
        sources = getattr(settings, "SCREENSHOT_SOURCES", ["/"])
        count = 0
        for node in queryset:
            for source in sources:
                try:
                    url = source.format(node=node, address=node.address, port=node.port)
                except Exception:
                    url = source
                if not url.startswith("http"):
                    url = f"http://{node.address}:{node.port}{url}"
                try:
                    path = capture_screenshot(url)
                except Exception as exc:  # pragma: no cover - selenium issues
                    self.message_user(request, f"{node}: {exc}", messages.ERROR)
                    continue
                sample = save_screenshot(
                    path, node=node, method="ADMIN", transaction_uuid=tx
                )
                if sample:
                    count += 1
        self.message_user(request, f"{count} screenshots captured", messages.SUCCESS)

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

        security_dir = Path(local_node.base_path or settings.BASE_DIR) / "security"
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

    def _sign_payload(self, private_key, payload: str) -> str:
        return base64.b64encode(
            private_key.sign(
                payload.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()

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
            "admin/nodes/node/rfid_sync_results.html",
            context,
        )

    def _process_import_from_node(self, node, payload, headers):
        result = self._init_rfid_result(node)
        url = f"http://{node.address}:{node.port}/nodes/rfid/export/"
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=5)
        except RequestException as exc:
            result["status"] = "error"
            result["errors"].append(str(exc))
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
        url = f"http://{node.address}:{node.port}/nodes/rfid/import/"
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=5)
        except RequestException as exc:
            result["status"] = "error"
            result["errors"].append(str(exc))
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

    @admin.action(description=_("Import RFIDs from selected"))
    def import_rfids_from_selected(self, request, queryset):
        nodes = list(queryset)
        local_node, private_key, error = self._load_local_node_credentials()
        if error:
            results = [self._skip_result(node, error) for node in nodes]
            return self._render_rfid_sync(request, "import", results, setup_error=error)

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
        signature = self._sign_payload(private_key, payload)
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

    @admin.action(description=_("Export RFIDs to selected"))
    def export_rfids_to_selected(self, request, queryset):
        nodes = list(queryset)
        local_node, private_key, error = self._load_local_node_credentials()
        if error:
            results = [self._skip_result(node, error) for node in nodes]
            return self._render_rfid_sync(request, "export", results, setup_error=error)

        if not nodes:
            return self._render_rfid_sync(
                request,
                "export",
                [],
                setup_error=_("No nodes selected."),
            )

        rfids = [serialize_rfid(tag) for tag in RFID.objects.all().order_by("label_id")]
        payload = json.dumps(
            {"requester": str(local_node.uuid), "rfids": rfids},
            separators=(",", ":"),
            sort_keys=True,
        )
        signature = self._sign_payload(private_key, payload)
        headers = {
            "Content-Type": "application/json",
            "X-Signature": signature,
        }

        results = []
        for node in nodes:
            if local_node.pk and node.pk == local_node.pk:
                results.append(self._skip_result(node, _("Skipped local node.")))
                continue
            results.append(self._post_export_to_node(node, payload, headers))

        return self._render_rfid_sync(request, "export", results)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["public_key_url"] = reverse(
                "admin:nodes_node_public_key", args=[object_id]
            )
        return super().changeform_view(
            request, object_id, form_url, extra_context=extra_context
        )


@admin.register(EmailOutbox)
class EmailOutboxAdmin(EntityModelAdmin):
    form = EmailOutboxAdminForm
    list_display = (
        "owner_label",
        "host",
        "port",
        "username",
        "use_tls",
        "use_ssl",
        "is_enabled",
    )
    change_form_template = "admin/nodes/emailoutbox/change_form.html"
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "Configuration",
            {
                "fields": (
                    "node",
                    "host",
                    "port",
                    "use_tls",
                    "use_ssl",
                    "from_email",
                    "is_enabled",
                )
            },
        ),
    )

    @admin.display(description="Owner")
    def owner_label(self, obj):
        return obj.owner_display()

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/test/",
                self.admin_site.admin_view(self.test_outbox),
                name="nodes_emailoutbox_test",
            )
        ]
        return custom + urls

    def test_outbox(self, request, object_id):
        outbox = self.get_object(request, object_id)
        if not outbox:
            self.message_user(request, "Unknown outbox", messages.ERROR)
            return redirect("..")
        recipient = request.user.email or outbox.username
        try:
            outbox.send_mail(
                "Test email",
                "This is a test email.",
                [recipient],
            )
            self.message_user(request, "Test email sent", messages.SUCCESS)
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(request, str(exc), messages.ERROR)
        return redirect("..")

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["test_url"] = reverse(
                "admin:nodes_emailoutbox_test", args=[object_id]
            )
        return super().changeform_view(request, object_id, form_url, extra_context)


class NodeRoleAdminForm(forms.ModelForm):
    nodes = forms.ModelMultipleChoiceField(
        queryset=Node.objects.all(),
        required=False,
        widget=FilteredSelectMultiple("Nodes", False),
    )

    class Meta:
        model = NodeRole
        fields = ("name", "description", "nodes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["nodes"].initial = self.instance.node_set.all()


@admin.register(NodeRole)
class NodeRoleAdmin(EntityModelAdmin):
    form = NodeRoleAdminForm
    list_display = ("name", "description", "registered", "default_features")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_registered=Count("node", distinct=True)).prefetch_related(
            "features"
        )

    @admin.display(description="Registered", ordering="_registered")
    def registered(self, obj):
        return getattr(obj, "_registered", obj.node_set.count())

    @admin.display(description="Default Features")
    def default_features(self, obj):
        features = [feature.display for feature in obj.features.all()]
        return ", ".join(features) if features else "—"

    def save_model(self, request, obj, form, change):
        obj.node_set.set(form.cleaned_data.get("nodes", []))


@admin.register(NodeFeature)
class NodeFeatureAdmin(EntityModelAdmin):
    filter_horizontal = ("roles",)
    list_display = (
        "display",
        "slug",
        "default_roles",
        "is_enabled_display",
        "available_actions",
    )
    actions = ["check_features_for_eligibility", "enable_selected_features"]
    readonly_fields = ("is_enabled",)
    search_fields = ("display", "slug")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("roles")

    @admin.display(description="Default Roles")
    def default_roles(self, obj):
        roles = [role.name for role in obj.roles.all()]
        return ", ".join(roles) if roles else "—"

    @admin.display(description="Is Enabled", boolean=True, ordering="is_enabled")
    def is_enabled_display(self, obj):
        return obj.is_enabled

    @admin.display(description="Actions")
    def available_actions(self, obj):
        if not obj.is_enabled:
            return "—"
        actions = obj.get_default_actions()
        if not actions:
            return "—"

        links = []
        for action in actions:
            try:
                url = reverse(action.url_name)
            except NoReverseMatch:
                links.append(action.label)
            else:
                links.append(format_html('<a href="{}">{}</a>', url, action.label))

        if not links:
            return "—"
        return format_html_join(" | ", "{}", ((link,) for link in links))

    def _manual_enablement_message(self, feature, node):
        if node is None:
            return (
                "Manual enablement is unavailable without a registered local node."
            )
        if feature.slug in Node.MANUAL_FEATURE_SLUGS:
            return "This feature can be enabled manually."
        return "This feature cannot be enabled manually."

    @admin.action(description="Check features for eligibility")
    def check_features_for_eligibility(self, request, queryset):
        from .feature_checks import feature_checks

        features = list(queryset)
        total = len(features)
        successes = 0
        node = Node.get_local()
        for feature in features:
            enablement_message = self._manual_enablement_message(feature, node)
            try:
                result = feature_checks.run(feature, node=node)
            except Exception as exc:  # pragma: no cover - defensive
                self.message_user(
                    request,
                    f"{feature.display}: {exc} {enablement_message}",
                    level=messages.ERROR,
                )
                continue
            if result is None:
                self.message_user(
                    request,
                    f"No check is configured for {feature.display}. {enablement_message}",
                    level=messages.WARNING,
                )
                continue
            message = result.message or (
                f"{feature.display} check {'passed' if result.success else 'failed'}."
            )
            self.message_user(
                request, f"{message} {enablement_message}", level=result.level
            )
            if result.success:
                successes += 1
        if total:
            self.message_user(
                request,
                f"Completed {successes} of {total} feature check(s) successfully.",
                level=messages.INFO,
            )

    @admin.action(description="Enable selected action")
    def enable_selected_features(self, request, queryset):
        node = Node.get_local()
        if node is None:
            self.message_user(
                request,
                "No local node is registered; unable to enable features manually.",
                level=messages.ERROR,
            )
            return

        manual_features = [
            feature
            for feature in queryset
            if feature.slug in Node.MANUAL_FEATURE_SLUGS
        ]
        non_manual_features = [
            feature
            for feature in queryset
            if feature.slug not in Node.MANUAL_FEATURE_SLUGS
        ]
        for feature in non_manual_features:
            self.message_user(
                request,
                f"{feature.display} cannot be enabled manually.",
                level=messages.WARNING,
            )

        if not manual_features:
            self.message_user(
                request,
                "None of the selected features can be enabled manually.",
                level=messages.WARNING,
            )
            return

        current_manual = set(
            node.features.filter(slug__in=Node.MANUAL_FEATURE_SLUGS).values_list(
                "slug", flat=True
            )
        )
        desired_manual = current_manual | {feature.slug for feature in manual_features}
        newly_enabled = desired_manual - current_manual
        if not newly_enabled:
            self.message_user(
                request,
                "Selected manual features are already enabled.",
                level=messages.INFO,
            )
            return

        node.update_manual_features(desired_manual)
        display_map = {feature.slug: feature.display for feature in manual_features}
        newly_enabled_names = [display_map[slug] for slug in sorted(newly_enabled)]
        self.message_user(
            request,
            "Enabled {} feature(s): {}".format(
                len(newly_enabled), ", ".join(newly_enabled_names)
            ),
            level=messages.SUCCESS,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "celery-report/",
                self.admin_site.admin_view(self.celery_report),
                name="nodes_nodefeature_celery_report",
            ),
            path(
                "view-waveform/",
                self.admin_site.admin_view(self.view_waveform),
                name="nodes_nodefeature_view_waveform",
            ),
            path(
                "take-screenshot/",
                self.admin_site.admin_view(self.take_screenshot),
                name="nodes_nodefeature_take_screenshot",
            ),
            path(
                "take-snapshot/",
                self.admin_site.admin_view(self.take_snapshot),
                name="nodes_nodefeature_take_snapshot",
            ),
            path(
                "view-stream/",
                self.admin_site.admin_view(self.view_stream),
                name="nodes_nodefeature_view_stream",
            ),
        ]
        return custom + urls

    def celery_report(self, request):
        period = resolve_period(request.GET.get("period"))
        now = timezone.now()
        window_end = now + period.delta
        log_window_start = now - period.delta

        scheduled_tasks = collect_scheduled_tasks(now, window_end)
        log_collection = collect_celery_log_entries(log_window_start, now)

        period_options = [
            {
                "key": candidate.key,
                "label": candidate.label,
                "selected": candidate.key == period.key,
                "url": f"?period={candidate.key}",
            }
            for candidate in iter_report_periods()
        ]

        context = {
            **self.admin_site.each_context(request),
            "title": _("Celery Report"),
            "period": period,
            "period_options": period_options,
            "current_time": now,
            "window_end": window_end,
            "log_window_start": log_window_start,
            "scheduled_tasks": scheduled_tasks,
            "log_entries": log_collection.entries,
            "log_sources": log_collection.checked_sources,
        }
        return TemplateResponse(
            request,
            "admin/nodes/nodefeature/celery_report.html",
            context,
        )

    def _ensure_feature_enabled(self, request, slug: str, action_label: str):
        try:
            feature = NodeFeature.objects.get(slug=slug)
        except NodeFeature.DoesNotExist:
            self.message_user(
                request,
                f"{action_label} is unavailable because the feature is not configured.",
                level=messages.ERROR,
            )
            return None
        if not feature.is_enabled:
            self.message_user(
                request,
                f"{feature.display} feature is not enabled on this node.",
                level=messages.WARNING,
            )
            return None
        return feature

    def view_waveform(self, request):
        feature = self._ensure_feature_enabled(
            request, "audio-capture", "View Waveform"
        )
        if not feature:
            return redirect("..")

        context = {
            **self.admin_site.each_context(request),
            "title": _("Audio Capture Waveform"),
            "feature": feature,
        }
        return TemplateResponse(
            request,
            "admin/nodes/nodefeature/view_waveform.html",
            context,
        )

    def take_screenshot(self, request):
        feature = self._ensure_feature_enabled(
            request, "screenshot-poll", "Take Screenshot"
        )
        if not feature:
            return redirect("..")
        url = request.build_absolute_uri("/")
        try:
            path = capture_screenshot(url)
        except Exception as exc:  # pragma: no cover - depends on selenium setup
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        node = Node.get_local()
        sample = save_screenshot(path, node=node, method="DEFAULT_ACTION")
        if not sample:
            self.message_user(
                request, "Duplicate screenshot; not saved", level=messages.INFO
            )
            return redirect("..")
        self.message_user(
            request, f"Screenshot saved to {sample.path}", level=messages.SUCCESS
        )
        try:
            change_url = reverse(
                "admin:nodes_contentsample_change", args=[sample.pk]
            )
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                "Screenshot saved but the admin page could not be resolved.",
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

    def take_snapshot(self, request):
        feature = self._ensure_feature_enabled(
            request, "rpi-camera", "Take a Snapshot"
        )
        if not feature:
            return redirect("..")
        try:
            path = capture_rpi_snapshot()
        except Exception as exc:  # pragma: no cover - depends on camera stack
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        node = Node.get_local()
        sample = save_screenshot(path, node=node, method="RPI_CAMERA")
        if not sample:
            self.message_user(
                request, "Duplicate snapshot; not saved", level=messages.INFO
            )
            return redirect("..")
        self.message_user(
            request, f"Snapshot saved to {sample.path}", level=messages.SUCCESS
        )
        try:
            change_url = reverse(
                "admin:nodes_contentsample_change", args=[sample.pk]
            )
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                "Snapshot saved but the admin page could not be resolved.",
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

    def view_stream(self, request):
        feature = self._ensure_feature_enabled(request, "rpi-camera", "View stream")
        if not feature:
            return redirect("..")

        configured_stream = getattr(settings, "RPI_CAMERA_STREAM_URL", "").strip()
        if configured_stream:
            stream_url = configured_stream
        else:
            base_uri = request.build_absolute_uri("/")
            parsed = urlsplit(base_uri)
            hostname = parsed.hostname or "127.0.0.1"
            port = getattr(settings, "RPI_CAMERA_STREAM_PORT", 8554)
            scheme = getattr(settings, "RPI_CAMERA_STREAM_SCHEME", "http")
            netloc = f"{hostname}:{port}" if port else hostname
            stream_url = urlunsplit((scheme, netloc, "/", "", ""))
        parsed_stream = urlsplit(stream_url)
        path = (parsed_stream.path or "").lower()
        query = (parsed_stream.query or "").lower()

        if parsed_stream.scheme in {"rtsp", "rtsps"}:
            embed_mode = "unsupported"
        elif any(path.endswith(ext) for ext in (".mjpg", ".mjpeg", ".jpeg", ".jpg", ".png")) or "action=stream" in query:
            embed_mode = "mjpeg"
        else:
            embed_mode = "iframe"

        context = {
            **self.admin_site.each_context(request),
            "title": _("Raspberry Pi Camera Stream"),
            "stream_url": stream_url,
            "stream_embed": embed_mode,
        }
        return TemplateResponse(
            request,
            "admin/nodes/nodefeature/view_stream.html",
            context,
        )


@admin.register(ContentTag)
class ContentTagAdmin(EntityModelAdmin):
    list_display = ("label", "slug")
    search_fields = ("label", "slug")


@admin.register(ContentClassifier)
class ContentClassifierAdmin(EntityModelAdmin):
    list_display = ("label", "slug", "kind", "run_by_default", "active")
    list_filter = ("kind", "run_by_default", "active")
    search_fields = ("label", "slug", "entrypoint")


class ContentClassificationInline(admin.TabularInline):
    model = ContentClassification
    extra = 0
    autocomplete_fields = ("classifier", "tag")


@admin.register(ContentSample)
class ContentSampleAdmin(EntityModelAdmin):
    list_display = ("name", "kind", "node", "user", "created_at")
    readonly_fields = ("created_at", "name", "user", "image_preview")
    inlines = (ContentClassificationInline,)
    list_filter = ("kind", "classifications__tag")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "from-clipboard/",
                self.admin_site.admin_view(self.add_from_clipboard),
                name="nodes_contentsample_from_clipboard",
            ),
            path(
                "capture/",
                self.admin_site.admin_view(self.capture_now),
                name="nodes_contentsample_capture",
            ),
        ]
        return custom + urls

    def add_from_clipboard(self, request):
        try:
            content = pyperclip.paste()
        except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
            self.message_user(request, f"Clipboard error: {exc}", level=messages.ERROR)
            return redirect("..")
        if not content:
            self.message_user(request, "Clipboard is empty.", level=messages.INFO)
            return redirect("..")
        if ContentSample.objects.filter(
            content=content, kind=ContentSample.TEXT
        ).exists():
            self.message_user(
                request, "Duplicate sample not created.", level=messages.INFO
            )
            return redirect("..")
        user = request.user if request.user.is_authenticated else None
        with suppress_default_classifiers():
            sample = ContentSample.objects.create(
                content=content, user=user, kind=ContentSample.TEXT
            )
        run_default_classifiers(sample)
        self.message_user(
            request, "Text sample added from clipboard.", level=messages.SUCCESS
        )
        return redirect("..")

    def capture_now(self, request):
        node = Node.get_local()
        url = request.build_absolute_uri("/")
        try:
            path = capture_screenshot(url)
        except Exception as exc:  # pragma: no cover - depends on selenium setup
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        sample = save_screenshot(path, node=node, method="ADMIN")
        if sample:
            self.message_user(request, f"Screenshot saved to {path}", messages.SUCCESS)
        else:
            self.message_user(request, "Duplicate screenshot; not saved", messages.INFO)
        return redirect("..")

    @admin.display(description="Screenshot")
    def image_preview(self, obj):
        if not obj or obj.kind != ContentSample.IMAGE or not obj.path:
            return ""
        file_path = Path(obj.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        if not file_path.exists():
            return "File not found"
        with file_path.open("rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return format_html(
            '<img src="data:image/png;base64,{}" style="max-width:100%;" />',
            encoded,
        )


@admin.register(NetMessage)
class NetMessageAdmin(EntityModelAdmin):
    class QuickSendForm(forms.ModelForm):
        class Meta:
            model = NetMessage
            fields = [
                "subject",
                "body",
                "attachments",
                "filter_node",
                "filter_node_feature",
                "filter_node_role",
                "filter_current_relation",
                "filter_installed_version",
                "filter_installed_revision",
                "target_limit",
            ]
            widgets = {"body": forms.Textarea(attrs={"rows": 4})}

    class NetMessageAdminForm(forms.ModelForm):
        class Meta:
            model = NetMessage
            fields = "__all__"
            widgets = {"body": forms.Textarea(attrs={"rows": 4})}

    change_list_template = "admin/nodes/netmessage/change_list.html"
    form = NetMessageAdminForm
    change_form_template = "admin/nodes/netmessage/change_form.html"
    list_display = (
        "subject",
        "body",
        "filter_node",
        "filter_node_role_display",
        "node_origin",
        "created",
        "target_limit_display",
        "complete",
    )
    search_fields = ("subject", "body")
    list_filter = ("complete", "filter_node_role", "filter_current_relation")
    ordering = ("-created",)
    readonly_fields = ("complete",)
    actions = ["send_messages"]
    fieldsets = (
        (None, {"fields": ("subject", "body")}),
        (
            "Filters",
            {
                "fields": (
                    "filter_node",
                    "filter_node_feature",
                    "filter_node_role",
                    "filter_current_relation",
                    "filter_installed_version",
                    "filter_installed_revision",
                )
            },
        ),
        ("Attachments", {"fields": ("attachments",)}),
        (
            "Propagation",
            {
                "fields": (
                    "node_origin",
                    "target_limit",
                    "propagated_to",
                    "complete",
                )
            },
        ),
    )
    quick_send_fieldsets = (
        (None, {"fields": ("subject", "body")}),
        (
            _("Filters"),
            {
                "fields": (
                    "filter_node",
                    "filter_node_feature",
                    "filter_node_role",
                    "filter_current_relation",
                    "filter_installed_version",
                    "filter_installed_revision",
                )
            },
        ),
        (
            _("Propagation"),
            {
                "fields": (
                    "target_limit",
                )
            },
        ),
    )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if self.has_add_permission(request):
            action = getattr(self, "send", None)
            if action is not None and "send" not in actions:
                actions["send"] = (
                    action,
                    "send",
                    getattr(action, "short_description", _("Send Net Message")),
                )
        return actions

    def send(self, request, queryset=None):
        return redirect(
            reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_send"
            )
        )

    send.label = _("Send Net Message")
    send.short_description = _("Send Net Message")

    def get_urls(self):
        urls = super().get_urls()
        opts = self.model._meta
        custom_urls = [
            path(
                "send/",
                self.admin_site.admin_view(self.send_tool_view),
                name=f"{opts.app_label}_{opts.model_name}_send",
            )
        ]
        return custom_urls + urls

    def send_tool_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied

        form_class = self.QuickSendForm
        if request.method == "POST":
            form = form_class(request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                obj.pk = None
                previous_skip_flag = getattr(self, "_skip_entity_user_datum", False)
                self._skip_entity_user_datum = True
                try:
                    self.save_model(request, obj, form, change=False)
                    self.save_related(request, form, formsets=[], change=False)
                finally:
                    self._skip_entity_user_datum = previous_skip_flag
                self.log_addition(
                    request,
                    obj,
                    self.construct_change_message(request, form, None),
                )
                obj.propagate()
                self.message_user(
                    request,
                    _("Net Message sent to the network."),
                    level=messages.SUCCESS,
                )
                changelist_url = reverse(
                    f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist"
                )
                return redirect(changelist_url)
        else:
            form = form_class()

        admin_form = helpers.AdminForm(form, self.quick_send_fieldsets, {})
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Send Net Message"),
            "adminform": admin_form,
            "media": self.media + form.media,
        }
        return TemplateResponse(
            request,
            "admin/nodes/netmessage/send.html",
            context,
        )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial = dict(initial) if initial else {}
        reply_to = request.GET.get("reply_to")
        if reply_to:
            try:
                message = (
                    NetMessage.objects.select_related("node_origin__role")
                    .get(pk=reply_to)
                )
            except (NetMessage.DoesNotExist, ValueError, TypeError):
                message = None
            if message:
                subject = (message.subject or "").strip()
                if subject:
                    if not subject.lower().startswith("re:"):
                        subject = f"Re: {subject}"
                else:
                    subject = "Re:"
                initial.setdefault("subject", subject[:64])
                if message.node_origin and "filter_node" not in initial:
                    initial["filter_node"] = message.node_origin.pk
        return initial

    def send_messages(self, request, queryset):
        for msg in queryset:
            msg.propagate()
        self.message_user(request, f"{queryset.count()} messages sent")

    send_messages.short_description = "Send selected messages"

    @admin.display(description="Role", ordering="filter_node_role")
    def filter_node_role_display(self, obj):
        return obj.filter_node_role

    @admin.display(description="TL", ordering="target_limit")
    def target_limit_display(self, obj):
        return obj.target_limit or ""
