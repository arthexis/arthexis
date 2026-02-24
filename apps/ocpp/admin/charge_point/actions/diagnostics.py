"""Diagnostics-related charger admin actions."""

import contextlib
import ipaddress
import socket
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from django.apps import apps
from django.conf import settings
from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext
from requests import RequestException

from apps.features.models import Feature
from apps.nodes.models import Node

from ....models import Charger
from .services import ActionServiceMixin


class DiagnosticsActionsMixin(ActionServiceMixin):
    """Admin actions for diagnostics request/download workflows."""

    class DiagnosticsDownloadError(Exception):
        """Raised when diagnostics downloads fail."""

    def _diagnostics_directory_for(self, user) -> tuple[Path, Path]:
        username = user.get_username() if callable(getattr(user, "get_username", None)) else getattr(user, "username", "")
        username_component = Path(str(username or getattr(user, "pk", "user"))).name or "user"
        user_dir = Path(settings.BASE_DIR) / "work" / username_component
        diagnostics_dir = user_dir / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        return diagnostics_dir, user_dir

    def _content_disposition_filename(self, header_value: str) -> str:
        for part in header_value.split(";"):
            candidate = part.strip()
            lower = candidate.lower()
            if lower.startswith("filename*="):
                value = candidate.split("=", 1)[1].strip()
                if value.lower().startswith("utf-8''"):
                    value = value[7:]
                return Path(unquote(value.strip('"'))).name
            if lower.startswith("filename="):
                return Path(candidate.split("=", 1)[1].strip().strip('"')).name
        return ""

    def _diagnostics_filename(self, charger: Charger, location: str, response) -> str:
        candidate = Path(urlparse(location).path or "").name
        content_disposition = response.headers.get("Content-Disposition") if hasattr(response, "headers") else None
        if content_disposition:
            candidate = self._content_disposition_filename(content_disposition) or candidate
        if not candidate:
            candidate = "diagnostics.log"
        suffix = "".join(Path(candidate).suffixes) or ".log"
        base_name = (candidate[: -len(suffix)] if suffix else candidate).rstrip(".") or "diagnostics"
        charger_slug = slugify(charger.charger_id or charger.display_name or str(charger.pk or "charger")) or "charger"
        return f"{charger_slug}-{slugify(base_name) or 'diagnostics'}-{timezone.now().strftime('%Y%m%d%H%M%S')}{suffix}"

    def _unique_diagnostics_path(self, directory: Path, filename: str) -> Path:
        suffix = "".join(Path(filename).suffixes)
        base = (filename[: -len(suffix)] if suffix else filename).rstrip(".") or "diagnostics"
        candidate = directory / f"{base}{suffix}"
        counter = 1
        while candidate.exists():
            candidate = directory / f"{base}-{counter}{suffix}"
            counter += 1
        return candidate


    def _is_safe_diagnostics_host(self, hostname: str) -> bool:
        """Return whether hostname resolves only to public, non-local addresses."""
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False
        for info in infos:
            address = info[4][0]
            ip_obj = ipaddress.ip_address(address)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved or ip_obj.is_multicast or ip_obj.is_unspecified:
                return False
        return True

    def _download_diagnostics(self, request, charger: Charger, location: str, diagnostics_dir: Path, user_dir: Path) -> tuple[Path, str]:
        parsed_url = urlparse(location)
        scheme = (parsed_url.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise self.DiagnosticsDownloadError(_("Diagnostics location must use HTTP or HTTPS."))
        if not parsed_url.hostname or not self._is_safe_diagnostics_host(parsed_url.hostname):
            raise self.DiagnosticsDownloadError(_("Diagnostics host is not allowed."))
        try:
            response = requests.get(location, stream=True, timeout=15)
        except RequestException as exc:
            raise self.DiagnosticsDownloadError(_("Failed to download diagnostics: %s") % exc) from exc
        try:
            if response.status_code != 200:
                raise self.DiagnosticsDownloadError(_("Diagnostics download returned status %s.") % response.status_code)
            destination = self._unique_diagnostics_path(diagnostics_dir, self._diagnostics_filename(charger, location, response))
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)
        except OSError as exc:
            raise self.DiagnosticsDownloadError(_("Unable to write diagnostics file: %s") % exc) from exc
        finally:
            with contextlib.suppress(Exception):
                response.close()
        asset_url = reverse("docs:readme-asset", kwargs={"source": "work", "asset": destination.relative_to(user_dir).as_posix()})
        return destination, request.build_absolute_uri(asset_url)

    def _prepare_diagnostics_payload(self, request, charger: Charger, *, expires_at):
        bucket = charger.ensure_diagnostics_bucket(expires_at=expires_at)
        location = request.build_absolute_uri(reverse("ocpp:media-bucket-upload", kwargs={"slug": bucket.slug}))
        payload = {"location": location}
        if bucket.expires_at:
            payload["stopTime"] = bucket.expires_at.isoformat()
        Charger.objects.filter(pk=charger.pk).update(diagnostics_bucket=bucket, diagnostics_location=location)
        charger.diagnostics_bucket = bucket
        charger.diagnostics_location = location
        return payload

    def _request_get_diagnostics(self, request, queryset, *, expires_at, success_message):
        requested = 0
        local_node = private_key = None
        remote_unavailable = False
        for charger in queryset:
            payload = self._prepare_diagnostics_payload(request, charger, expires_at=expires_at)
            if charger.is_local:
                if self._send_local_ocpp_call(request, charger, action="GetDiagnostics", payload=payload, pending_payload={"location": payload["location"]}):
                    requested += 1
                continue
            if not charger.allow_remote:
                self.message_user(request, f"{charger}: remote administration is disabled.", level=messages.ERROR)
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(request, local_node, private_key, charger, "request-diagnostics", payload)
            if success:
                self._apply_remote_updates(charger, updates)
                requested += 1
        if requested:
            self.message_user(request, success_message(requested))

    @admin.action(description="Request CP diagnostics")
    def request_cp_diagnostics(self, request, queryset):
        self._request_get_diagnostics(request, queryset, expires_at=timezone.now() + timedelta(days=30), success_message=lambda count: ngettext("Requested diagnostics from %(count)d charger.", "Requested diagnostics from %(count)d chargers.", count) % {"count": count})

    @admin.action(description="Setup CP Diagnostics")
    def setup_cp_diagnostics(self, request, queryset):
        self._request_get_diagnostics(request, queryset, expires_at=timezone.now() + timedelta(days=30), success_message=lambda count: ngettext("Set up diagnostics upload for %(count)d charger.", "Set up diagnostics upload for %(count)d chargers.", count) % {"count": count})

    @admin.action(description=_("Configure local FTP server"))
    def configure_local_ftp_server(self, request, queryset):
        feature = Feature.objects.select_related("node_feature").filter(slug=Charger.FTP_REPORTS_FEATURE_SLUG).first()
        if not feature:
            self.message_user(request, _("Suite feature %(slug)s is missing; cannot configure FTP servers.") % {"slug": Charger.FTP_REPORTS_FEATURE_SLUG}, messages.WARNING)
            return
        local_node = Node.get_local()
        chargers = list(queryset.select_related("manager_node"))
        if not chargers:
            self.message_user(request, _("No chargers were selected for FTP configuration."), messages.INFO)
            return
        try:
            FTPServer = apps.get_model("ftp", "FTPServer")
        except LookupError:
            self.message_user(request, _("FTP application is not installed; cannot configure FTP servers."), messages.ERROR)
            return
        by_node: dict[int, list[Charger]] = {}
        skipped_missing_node = 0
        for charger in chargers:
            node = charger.manager_node or local_node
            if not node:
                skipped_missing_node += 1
                continue
            by_node.setdefault(node.pk, []).append(charger)
        configured = skipped_feature = 0
        for chargers_for_node in by_node.values():
            node = chargers_for_node[0].manager_node or local_node
            if not node:
                skipped_missing_node += len(chargers_for_node)
                continue
            if not feature.is_enabled_for_node(node):
                skipped_feature += len(chargers_for_node)
                continue
            server, created = FTPServer.objects.get_or_create(node=node, defaults={"enabled": True})
            if not created and not server.enabled:
                FTPServer.objects.filter(pk=server.pk).update(enabled=True)
                server.enabled = True
            location = chargers_for_node[0].build_report_ftp_location(server=server, node=node)
            updates = {"ftp_server": server}
            if location:
                updates["diagnostics_location"] = location
            configured += Charger.objects.filter(pk__in=[charger.pk for charger in chargers_for_node]).update(**updates)
        if configured:
            self.message_user(request, ngettext("Configured local FTP server for %(count)d charger.", "Configured local FTP server for %(count)d chargers.", configured) % {"count": configured}, messages.SUCCESS)
        else:
            self.message_user(request, _("No FTP servers were configured for the selected chargers."), messages.WARNING)
        if skipped_feature:
            self.message_user(request, ngettext("Skipped %(count)d charger because the FTP suite feature is disabled for its node.", "Skipped %(count)d chargers because the FTP suite feature is disabled for their nodes.", skipped_feature) % {"count": skipped_feature}, messages.WARNING)
        if skipped_missing_node:
            self.message_user(request, ngettext("Skipped %(count)d charger because no node is available for FTP configuration.", "Skipped %(count)d chargers because no node is available for FTP configuration.", skipped_missing_node) % {"count": skipped_missing_node}, messages.WARNING)

    @admin.action(description="Get diagnostics")
    def get_diagnostics(self, request, queryset):
        diagnostics_dir, user_dir = self._diagnostics_directory_for(request.user)
        successes: list[tuple[Charger, str, Path]] = []
        for charger in queryset:
            location = (charger.diagnostics_location or "").strip()
            if not location:
                self.message_user(request, _("%(charger)s: no diagnostics location reported.") % {"charger": charger}, level=messages.WARNING)
                continue
            try:
                destination, asset_url = self._download_diagnostics(request, charger, location, diagnostics_dir, user_dir)
            except self.DiagnosticsDownloadError as exc:
                self.message_user(request, _("%(charger)s: %(error)s") % {"charger": charger, "error": exc}, level=messages.ERROR)
                continue
            successes.append((charger, asset_url, destination))
        if successes:
            summary = ngettext("Retrieved diagnostics for %(count)d charger.", "Retrieved diagnostics for %(count)d chargers.", len(successes)) % {"count": len(successes)}
            details = format_html_join("", "<li>{}: <a href=\"{}\" target=\"_blank\">{}</a> (<code>{}</code>)</li>", ((charger, url, destination.name, destination) for charger, url, destination in successes))
            self.message_user(request, format_html("{}<ul>{}</ul>", summary, details), level=messages.SUCCESS)
