import base64
import contextlib
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.apps import apps
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext
from requests import RequestException

from apps.cards.models import RFID as CoreRFID
from apps.features.models import Feature
from apps.nodes.models import Node
from apps.protocols.decorators import protocol_call

from ... import store
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ...models import Charger, ChargerConfiguration, Simulator


class ChargerAdminActionsMixin:
    class DiagnosticsDownloadError(Exception):
        """Raised when diagnostics downloads fail."""

    def _diagnostics_directory_for(self, user) -> tuple[Path, Path]:
        username = getattr(user, "get_username", None)
        if callable(username):
            username = username()
        else:
            username = getattr(user, "username", "")
        if not username:
            username = str(getattr(user, "pk", "user"))
        username_component = Path(str(username)).name or "user"
        base_dir = Path(settings.BASE_DIR)
        user_dir = base_dir / "work" / username_component
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
                value = candidate.split("=", 1)[1].strip().strip('"')
                return Path(value).name
        return ""

    def _diagnostics_filename(self, charger: Charger, location: str, response) -> str:
        parsed = urlparse(location)
        candidate = Path(parsed.path or "").name
        header_name = ""
        content_disposition = response.headers.get("Content-Disposition") if hasattr(response, "headers") else None
        if content_disposition:
            header_name = self._content_disposition_filename(content_disposition)
        if header_name:
            candidate = header_name
        if not candidate:
            candidate = "diagnostics.log"
        path_candidate = Path(candidate)
        suffix = "".join(path_candidate.suffixes)
        if suffix:
            base_name = candidate[: -len(suffix)]
        else:
            base_name = candidate
            suffix = ".log"
        base_name = base_name.rstrip(".")
        if not base_name:
            base_name = "diagnostics"
        charger_slug = slugify(charger.charger_id or charger.display_name or str(charger.pk or "charger"))
        if not charger_slug:
            charger_slug = "charger"
        diagnostics_slug = slugify(base_name) or "diagnostics"
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        return f"{charger_slug}-{diagnostics_slug}-{timestamp}{suffix}"

    def _unique_diagnostics_path(self, directory: Path, filename: str) -> Path:
        base_path = Path(filename)
        suffix = "".join(base_path.suffixes)
        if suffix:
            base_name = filename[: -len(suffix)]
        else:
            base_name = filename
            suffix = ""
        base_name = base_name.rstrip(".") or "diagnostics"
        candidate = directory / f"{base_name}{suffix}"
        counter = 1
        while candidate.exists():
            candidate = directory / f"{base_name}-{counter}{suffix}"
            counter += 1
        return candidate

    def _download_diagnostics(
        self,
        request,
        charger: Charger,
        location: str,
        diagnostics_dir: Path,
        user_dir: Path,
    ) -> tuple[Path, str]:
        parsed = urlparse(location)
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise self.DiagnosticsDownloadError(
                _("Diagnostics location must use HTTP or HTTPS.")
            )
        try:
            response = requests.get(location, stream=True, timeout=15)
        except RequestException as exc:
            raise self.DiagnosticsDownloadError(
                _("Failed to download diagnostics: %s") % exc
            ) from exc
        try:
            if response.status_code != 200:
                raise self.DiagnosticsDownloadError(
                    _("Diagnostics download returned status %s.")
                    % response.status_code
                )
            filename = self._diagnostics_filename(charger, location, response)
            destination = self._unique_diagnostics_path(diagnostics_dir, filename)
            try:
                with destination.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        handle.write(chunk)
            except OSError as exc:
                raise self.DiagnosticsDownloadError(
                    _("Unable to write diagnostics file: %s") % exc
                ) from exc
        finally:
            with contextlib.suppress(Exception):
                response.close()
        relative_asset = destination.relative_to(user_dir).as_posix()
        asset_url = reverse(
            "docs:readme-asset",
            kwargs={"source": "work", "asset": relative_asset},
        )
        absolute_url = request.build_absolute_uri(asset_url)
        return destination, absolute_url

    def _prepare_remote_credentials(self, request):
        local = Node.get_local()
        if not local or not local.uuid:
            self.message_user(
                request,
                "Local node is not registered; remote actions are unavailable.",
                level=messages.ERROR,
            )
            return None, None
        private_key = local.get_private_key()
        if private_key is None:
            self.message_user(
                request,
                "Local node private key is unavailable; remote actions are disabled.",
                level=messages.ERROR,
            )
            return None, None
        return local, private_key

    def _call_remote_action(
        self,
        request,
        local_node: Node,
        private_key,
        charger: Charger,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        if not charger.node_origin:
            self.message_user(
                request,
                f"{charger}: remote node information is missing.",
                level=messages.ERROR,
            )
            return False, {}
        origin = charger.node_origin
        if not origin.port:
            self.message_user(
                request,
                f"{charger}: remote node port is not configured.",
                level=messages.ERROR,
            )
            return False, {}

        if not origin.get_remote_host_candidates():
            self.message_user(
                request,
                f"{charger}: remote node connection details are incomplete.",
                level=messages.ERROR,
            )
            return False, {}

        payload: dict[str, Any] = {
            "requester": str(local_node.uuid),
            "requester_mac": local_node.mac_address,
            "requester_public_key": local_node.public_key,
            "charger_id": charger.charger_id,
            "connector_id": charger.connector_id,
            "action": action,
        }
        if extra:
            payload.update(extra)

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}
        try:
            signature = private_key.sign(
                payload_json.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            headers["X-Signature"] = base64.b64encode(signature).decode()
        except Exception:
            self.message_user(
                request,
                "Unable to sign remote action payload; remote action aborted.",
                level=messages.ERROR,
            )
            return False, {}

        url = next(
            origin.iter_remote_urls("/nodes/network/chargers/action/"),
            "",
        )
        if not url:
            self.message_user(
                request,
                f"{charger}: no reachable hosts were reported for the remote node.",
                level=messages.ERROR,
            )
            return False, {}
        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
        except RequestException as exc:
            self.message_user(
                request,
                f"{charger}: failed to contact remote node ({exc}).",
                level=messages.ERROR,
            )
            return False, {}

        try:
            data = response.json()
        except ValueError:
            self.message_user(
                request,
                f"{charger}: invalid response from remote node.",
                level=messages.ERROR,
            )
            return False, {}

        if response.status_code != 200 or data.get("status") != "ok":
            detail = data.get("detail") if isinstance(data, dict) else None
            if not detail:
                detail = response.text or "Remote node rejected the request."
            self.message_user(
                request,
                f"{charger}: {detail}",
                level=messages.ERROR,
            )
            return False, {}

        updates = data.get("updates", {}) if isinstance(data, dict) else {}
        if not isinstance(updates, dict):
            updates = {}
        return True, updates

    def _apply_remote_updates(self, charger: Charger, updates: dict[str, Any]) -> None:
        if not updates:
            return

        applied: dict[str, Any] = {}
        for field, value in updates.items():
            if field in self._REMOTE_DATETIME_FIELDS and isinstance(value, str):
                parsed = parse_datetime(value)
                if parsed and timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                applied[field] = parsed
            else:
                applied[field] = value

        Charger.objects.filter(pk=charger.pk).update(**applied)
        for field, value in applied.items():
            setattr(charger, field, value)

    def _prepare_diagnostics_payload(self, request, charger: Charger, *, expires_at):
        bucket = charger.ensure_diagnostics_bucket(expires_at=expires_at)
        upload_path = reverse(
            "ocpp:media-bucket-upload", kwargs={"slug": bucket.slug}
        )
        location = request.build_absolute_uri(upload_path)
        payload: dict[str, object] = {"location": location}
        if bucket.expires_at:
            payload["stopTime"] = bucket.expires_at.isoformat()
        Charger.objects.filter(pk=charger.pk).update(
            diagnostics_bucket=bucket, diagnostics_location=location
        )
        charger.diagnostics_bucket = bucket
        charger.diagnostics_location = location
        return payload

    def _request_get_diagnostics(self, request, queryset, *, expires_at, success_message):
        requested = 0
        local_node = None
        private_key = None
        remote_unavailable = False

        for charger in queryset:
            payload = self._prepare_diagnostics_payload(
                request, charger, expires_at=expires_at
            )

            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                msg = json.dumps([2, message_id, "GetDiagnostics", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send GetDiagnostics ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "GetDiagnostics",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "location": payload["location"],
                        "requested_at": timezone.now(),
                    },
                )
                requested += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "request-diagnostics",
                payload,
            )
            if success:
                self._apply_remote_updates(charger, updates)
                requested += 1

        if requested:
            self.message_user(request, success_message(requested))

    @admin.action(description="Request CP diagnostics")
    def request_cp_diagnostics(self, request, queryset):
        expiration = timezone.now() + timedelta(days=30)

        def success_message(count):
            return (
                ngettext(
                    "Requested diagnostics from %(count)d charger.",
                    "Requested diagnostics from %(count)d chargers.",
                    count,
                )
                % {"count": count}
            )

        self._request_get_diagnostics(
            request,
            queryset,
            expires_at=expiration,
            success_message=success_message,
        )

    @admin.action(description="Setup CP Diagnostics")
    def setup_cp_diagnostics(self, request, queryset):
        expiration = timezone.now() + timedelta(days=30)

        def success_message(count):
            return (
                ngettext(
                    "Set up diagnostics upload for %(count)d charger.",
                    "Set up diagnostics upload for %(count)d chargers.",
                    count,
                )
                % {"count": count}
            )

        self._request_get_diagnostics(
            request,
            queryset,
            expires_at=expiration,
            success_message=success_message,
        )

    @admin.action(description=_("Configure local FTP server"))
    def configure_local_ftp_server(self, request, queryset):
        feature = (
            Feature.objects.select_related("node_feature")
            .filter(slug=Charger.FTP_REPORTS_FEATURE_SLUG)
            .first()
        )
        if not feature:
            self.message_user(
                request,
                _("Suite feature %(slug)s is missing; cannot configure FTP servers.")
                % {"slug": Charger.FTP_REPORTS_FEATURE_SLUG},
                messages.WARNING,
            )
            return
        local_node = Node.get_local()
        chargers = list(queryset.select_related("manager_node"))
        if not chargers:
            self.message_user(
                request,
                _("No chargers were selected for FTP configuration."),
                messages.INFO,
            )
            return

        try:
            FTPServer = apps.get_model("ftp", "FTPServer")
        except LookupError:
            self.message_user(
                request,
                _("FTP application is not installed; cannot configure FTP servers."),
                messages.ERROR,
            )
            return

        by_node: dict[int, list[Charger]] = {}
        skipped_missing_node = 0
        for charger in chargers:
            node = charger.manager_node or local_node
            if not node:
                skipped_missing_node += 1
                continue
            by_node.setdefault(node.pk, []).append(charger)

        configured = 0
        skipped_feature = 0
        for chargers_for_node in by_node.values():
            node = chargers_for_node[0].manager_node or local_node
            if not node:
                skipped_missing_node += len(chargers_for_node)
                continue
            if not feature.is_enabled_for_node(node):
                skipped_feature += len(chargers_for_node)
                continue

            server, created = FTPServer.objects.get_or_create(
                node=node,
                defaults={"enabled": True},
            )
            if not created and not server.enabled:
                FTPServer.objects.filter(pk=server.pk).update(enabled=True)
                server.enabled = True

            location = chargers_for_node[0].build_report_ftp_location(
                server=server,
                node=node,
            )
            updates = {"ftp_server": server}
            if location:
                updates["diagnostics_location"] = location
            configured += Charger.objects.filter(
                pk__in=[charger.pk for charger in chargers_for_node]
            ).update(**updates)

        if configured:
            self.message_user(
                request,
                ngettext(
                    "Configured local FTP server for %(count)d charger.",
                    "Configured local FTP server for %(count)d chargers.",
                    configured,
                )
                % {"count": configured},
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No FTP servers were configured for the selected chargers."),
                messages.WARNING,
            )
        if skipped_feature:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d charger because the FTP suite feature is disabled for its node.",
                    "Skipped %(count)d chargers because the FTP suite feature is disabled for their nodes.",
                    skipped_feature,
                )
                % {"count": skipped_feature},
                messages.WARNING,
            )
        if skipped_missing_node:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d charger because no node is available for FTP configuration.",
                    "Skipped %(count)d chargers because no node is available for FTP configuration.",
                    skipped_missing_node,
                )
                % {"count": skipped_missing_node},
                messages.WARNING,
            )

    @admin.action(description="Get diagnostics")
    def get_diagnostics(self, request, queryset):
        diagnostics_dir, user_dir = self._diagnostics_directory_for(request.user)
        successes: list[tuple[Charger, str, Path]] = []
        for charger in queryset:
            location = (charger.diagnostics_location or "").strip()
            if not location:
                self.message_user(
                    request,
                    _("%(charger)s: no diagnostics location reported.")
                    % {"charger": charger},
                    level=messages.WARNING,
                )
                continue
            try:
                destination, asset_url = self._download_diagnostics(
                    request,
                    charger,
                    location,
                    diagnostics_dir,
                    user_dir,
                )
            except self.DiagnosticsDownloadError as exc:
                self.message_user(
                    request,
                    _("%(charger)s: %(error)s")
                    % {"charger": charger, "error": exc},
                    level=messages.ERROR,
                )
                continue
            successes.append((charger, asset_url, destination))

        if successes:
            summary = ngettext(
                "Retrieved diagnostics for %(count)d charger.",
                "Retrieved diagnostics for %(count)d chargers.",
                len(successes),
            ) % {"count": len(successes)}
            details = format_html_join(
                "",
                "<li>{}: <a href=\"{}\" target=\"_blank\">{}</a> (<code>{}</code>)</li>",
                (
                    (charger, url, destination.name, destination)
                    for charger, url, destination in successes
                ),
            )
            message = format_html("{}<ul>{}</ul>", summary, details)
            self.message_user(request, message, level=messages.SUCCESS)

    def _build_local_authorization_list(self) -> list[dict[str, object]]:
        """Return the payload for SendLocalList with released RFIDs."""

        entries: list[dict[str, object]] = []
        standard_status = "Accepted"  # OCPP 1.6 idTagInfo status value
        queryset = (
            CoreRFID.objects.filter(released=True).order_by("rfid").only("rfid")
        )
        for tag in queryset.iterator():
            entry: dict[str, object] = {"idTag": tag.rfid}
            entry["idTagInfo"] = {"status": standard_status}
            entries.append(entry)
        return entries

    def _build_purge_summaries(self, queryset):
        target_chargers: dict[int, Charger] = {}

        for charger in queryset:
            for target in charger._target_chargers():
                target_chargers[target.pk] = target

        summaries: dict[str, dict[str, object]] = {}
        for target in target_chargers.values():
            key = target.charger_id
            summary = summaries.get(key)
            if summary is None:
                summary = {
                    "charger": target,
                    "display_name": self._charger_display_name(target),
                    "transactions": 0,
                    "meter_values": 0,
                }
                summaries[key] = summary
            elif summary["charger"].connector_id is not None and target.connector_id is None:
                summary["charger"] = target
                summary["display_name"] = self._charger_display_name(target)

            summary["transactions"] += target.transactions.count()
            summary["meter_values"] += target.meter_values.count()

        for summary in summaries.values():
            summary["total_rows"] = summary["transactions"] + summary["meter_values"]

        return sorted(
            summaries.values(), key=lambda item: item["display_name"].lower()
        )

    @admin.action(description=_("Clear all selected CP data"))
    def purge_data(self, request, queryset):
        purge_summaries = self._build_purge_summaries(queryset)

        for charger in queryset:
            charger.purge()

        total_rows = sum(summary["total_rows"] for summary in purge_summaries)
        self.message_user(
            request,
            _("Purged %(rows)s rows across %(count)s charge points.")
            % {"rows": total_rows, "count": len(purge_summaries)},
        )
        return None

    @admin.action(description="Re-check Charger Status")
    def recheck_charger_status(self, request, queryset):
        requested = 0
        for charger in queryset:
            connector_value = charger.connector_id
            ws = store.get_connection(charger.charger_id, connector_value)
            if ws is None:
                self.message_user(
                    request,
                    f"{charger}: no active connection",
                    level=messages.ERROR,
                )
                continue
            payload: dict[str, object] = {"requestedMessage": "StatusNotification"}
            trigger_connector: int | None = None
            if connector_value is not None:
                payload["connectorId"] = connector_value
                trigger_connector = connector_value
            message_id = uuid.uuid4().hex
            msg = json.dumps([2, message_id, "TriggerMessage", payload])
            try:
                async_to_sync(ws.send)(msg)
            except Exception as exc:  # pragma: no cover - network error
                self.message_user(
                    request,
                    f"{charger}: failed to send TriggerMessage ({exc})",
                    level=messages.ERROR,
                )
                continue
            log_key = store.identity_key(charger.charger_id, connector_value)
            store.add_log(log_key, f"< {msg}", log_type="charger")
            store.register_pending_call(
                message_id,
                {
                    "action": "TriggerMessage",
                    "charger_id": charger.charger_id,
                    "connector_id": connector_value,
                    "log_key": log_key,
                    "trigger_target": "StatusNotification",
                    "trigger_connector": trigger_connector,
                    "requested_at": timezone.now(),
                },
            )
            store.schedule_call_timeout(
                message_id,
                timeout=5.0,
                action="TriggerMessage",
                log_key=log_key,
                message="TriggerMessage StatusNotification timed out",
            )
            requested += 1
        if requested:
            self.message_user(
                request,
                f"Requested status update from {requested} charger(s)",
            )

    @admin.action(description="Fetch CP configuration")
    def fetch_cp_configuration(self, request, queryset):
        fetched = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {}
                msg = json.dumps([2, message_id, "GetConfiguration", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send GetConfiguration ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "GetConfiguration",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    timeout=5.0,
                    action="GetConfiguration",
                    log_key=log_key,
                    message=(
                        "GetConfiguration timed out: charger did not respond"
                        " (operation may not be supported)"
                    ),
                )
                fetched += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "get-configuration",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                fetched += 1

        if fetched:
            self.message_user(
                request,
                f"Requested configuration from {fetched} charger(s)",
            )

    @admin.action(description="Toggle RFID Authentication")
    def toggle_rfid_authentication(self, request, queryset):
        enabled = 0
        disabled = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            new_value = not charger.require_rfid
            if charger.is_local:
                Charger.objects.filter(pk=charger.pk).update(require_rfid=new_value)
                charger.require_rfid = new_value
                if new_value:
                    enabled += 1
                else:
                    disabled += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "toggle-rfid",
                {"enable": new_value},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                if charger.require_rfid:
                    enabled += 1
                else:
                    disabled += 1

        if enabled or disabled:
            changes = []
            if enabled:
                changes.append(f"enabled for {enabled} charger(s)")
            if disabled:
                changes.append(f"disabled for {disabled} charger(s)")
            summary = "; ".join(changes)
            self.message_user(
                request,
                f"Updated RFID authentication: {summary}",
            )

    @admin.action(description="Send Local RFIDs to CP")
    def send_rfid_list_to_evcs(self, request, queryset):
        authorization_list = self._build_local_authorization_list()
        update_type = "Full"
        sent = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            list_version = (charger.local_auth_list_version or 0) + 1
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {
                    "listVersion": list_version,
                    "updateType": update_type,
                    "localAuthorizationList": authorization_list,
                }
                msg = json.dumps([2, message_id, "SendLocalList", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send SendLocalList ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "SendLocalList",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "list_version": list_version,
                        "list_size": len(authorization_list),
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="SendLocalList",
                    log_key=log_key,
                    message="SendLocalList request timed out",
                )
                sent += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            extra = {
                "local_authorization_list": [entry.copy() for entry in authorization_list],
                "list_version": list_version,
                "update_type": update_type,
            }
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "send-local-rfid-list",
                extra,
            )
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1

        if sent:
            self.message_user(
                request,
                f"Sent SendLocalList to {sent} charger(s)",
            )

    @admin.action(description="Update RFIDs from EVCS")
    def update_rfids_from_evcs(self, request, queryset):
        requested = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload: dict[str, object] = {}
                msg = json.dumps([2, message_id, "GetLocalListVersion", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send GetLocalListVersion ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "GetLocalListVersion",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="GetLocalListVersion",
                    log_key=log_key,
                    message="GetLocalListVersion request timed out",
                )
                requested += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "get-local-list-version",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                requested += 1

        if requested:
            self.message_user(
                request,
                f"Requested GetLocalListVersion from {requested} charger(s)",
            )

    def _dispatch_change_availability(self, request, queryset, availability_type: str):
        sent = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                connector_id = connector_value if connector_value is not None else 0
                message_id = uuid.uuid4().hex
                payload = {"connectorId": connector_id, "type": availability_type}
                msg = json.dumps([2, message_id, "ChangeAvailability", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ChangeAvailability ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                timestamp = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ChangeAvailability",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "availability_type": availability_type,
                        "requested_at": timestamp,
                    },
                )
                updates = {
                    "availability_requested_state": availability_type,
                    "availability_requested_at": timestamp,
                    "availability_request_status": "",
                    "availability_request_status_at": None,
                    "availability_request_details": "",
                }
                Charger.objects.filter(pk=charger.pk).update(**updates)
                for field, value in updates.items():
                    setattr(charger, field, value)
                sent += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "change-availability",
                {"availability_type": availability_type},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1

        if sent:
            self.message_user(
                request,
                f"Sent ChangeAvailability ({availability_type}) to {sent} charger(s)",
            )

    @admin.action(description="Set availability to Operative")
    def change_availability_operative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Operative")

    @admin.action(description="Set availability to Inoperative")
    def change_availability_inoperative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Inoperative")

    def _set_availability_state(
        self, request, queryset, availability_state: str
    ) -> None:
        updated = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                timestamp = timezone.now()
                updates = {
                    "availability_state": availability_state,
                    "availability_state_updated_at": timestamp,
                }
                Charger.objects.filter(pk=charger.pk).update(**updates)
                for field, value in updates.items():
                    setattr(charger, field, value)
                updated += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "set-availability-state",
                {"availability_state": availability_state},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                updated += 1

        if updated:
            self.message_user(
                request,
                f"Updated availability to {availability_state} for {updated} charger(s)",
            )

    @admin.action(description="Mark availability as Operative")
    def set_availability_state_operative(self, request, queryset):
        self._set_availability_state(request, queryset, "Operative")

    @admin.action(description="Mark availability as Inoperative")
    def set_availability_state_inoperative(self, request, queryset):
        self._set_availability_state(request, queryset, "Inoperative")

    @admin.action(description="Clear charger authorization cache")
    def clear_authorization_cache(self, request, queryset):
        cleared = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                msg = json.dumps([2, message_id, "ClearCache", {}])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ClearCache ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                requested_at = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ClearCache",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": requested_at,
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="ClearCache",
                    log_key=log_key,
                )
                cleared += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, _updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "clear-cache",
            )
            if success:
                cleared += 1

        if cleared:
            self.message_user(
                request,
                f"Sent ClearCache to {cleared} charger(s)",
            )

    @protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ClearChargingProfile")
    @admin.action(description="Clear charging profiles")
    def clear_charging_profiles(self, request, queryset):
        cleared = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            connector_value = 0
            if charger.is_local:
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload: dict[str, object] = {}
                msg = json.dumps([2, message_id, "ClearChargingProfile", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ClearChargingProfile ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                requested_at = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ClearChargingProfile",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": requested_at,
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="ClearChargingProfile",
                    log_key=log_key,
                )
                cleared += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, _updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "clear-charging-profile",
            )
            if success:
                cleared += 1

        if cleared:
            self.message_user(
                request,
                f"Sent ClearChargingProfile to {cleared} charger(s)",
            )

    @admin.action(description="Unlock connector")
    def unlock_connector(self, request, queryset):
        unlocked = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            connector_value = charger.connector_id
            if connector_value in (None, 0):
                self.message_user(
                    request,
                    f"{charger}: connector id is required to send UnlockConnector.",
                    level=messages.ERROR,
                )
                continue

            if charger.is_local:
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {"connectorId": connector_value}
                msg = json.dumps([2, message_id, "UnlockConnector", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send UnlockConnector ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                requested_at = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "UnlockConnector",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": requested_at,
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="UnlockConnector",
                    log_key=log_key,
                    message="UnlockConnector request timed out",
                )
                unlocked += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "unlock-connector",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                unlocked += 1

        if unlocked:
            self.message_user(
                request,
                f"Sent UnlockConnector to {unlocked} charger(s)",
            )

    @admin.action(description="Remote stop active transaction")
    def remote_stop_transaction(self, request, queryset):
        stopped = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                tx_obj = store.get_transaction(charger.charger_id, connector_value)
                if tx_obj is None:
                    self.message_user(
                        request,
                        f"{charger}: no active transaction",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {"transactionId": tx_obj.pk}
                msg = json.dumps([
                    2,
                    message_id,
                    "RemoteStopTransaction",
                    payload,
                ])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send RemoteStopTransaction ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "RemoteStopTransaction",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "transaction_id": tx_obj.pk,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                stopped += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "remote-stop",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                stopped += 1

        if stopped:
            self.message_user(
                request,
                f"Sent RemoteStopTransaction to {stopped} charger(s)",
            )

    @admin.action(description="Reset charger (soft)")
    def reset_chargers(self, request, queryset):
        reset = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                tx_obj = store.get_transaction(charger.charger_id, connector_value)
                if tx_obj is not None:
                    self.message_user(
                        request,
                        (
                            f"{charger}: reset skipped because a session is active; "
                            "stop the session first."
                        ),
                        level=messages.WARNING,
                    )
                    continue
                message_id = uuid.uuid4().hex
                msg = json.dumps([
                    2,
                    message_id,
                    "Reset",
                    {"type": "Soft"},
                ])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send Reset ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "Reset",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    timeout=5.0,
                    action="Reset",
                    log_key=log_key,
                    message="Reset timed out: charger did not respond",
                )
                reset += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "reset",
                {"reset_type": "Soft"},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                reset += 1

        if reset:
            self.message_user(
                request,
                f"Sent Reset to {reset} charger(s)",
            )

    def _simulator_base_name(self, charger: Charger) -> str:
        display_name = self._charger_display_name(charger)
        connector_suffix = ""
        if charger.connector_id is not None:
            connector_suffix = f" {charger.connector_label}"
        base = f"{display_name}{connector_suffix} Simulator".strip()
        return base or "Charge Point Simulator"

    def _trim_with_suffix(self, base: str, suffix: str, *, max_length: int) -> str:
        base = base[: max_length - len(suffix)] if len(base) + len(suffix) > max_length else base
        return f"{base}{suffix}"

    def _unique_simulator_name(self, base: str) -> str:
        base = (base or "Simulator").strip()
        max_length = Simulator._meta.get_field("name").max_length
        base = base[:max_length]
        candidate = base or "Simulator"
        counter = 2
        while Simulator.objects.filter(name=candidate).exists():
            suffix = f" ({counter})"
            candidate = self._trim_with_suffix(base or "Simulator", suffix, max_length=max_length)
            counter += 1
        return candidate

    def _simulator_cp_path_base(self, charger: Charger) -> str:
        path = (charger.last_path or "").strip().strip("/")
        if not path:
            path = charger.charger_id.strip().strip("/")
        connector_slug = charger.connector_slug
        if connector_slug and connector_slug != Charger.AGGREGATE_CONNECTOR_SLUG:
            path = f"{path}-{connector_slug}" if path else connector_slug
        return path or "SIMULATOR"

    def _unique_simulator_cp_path(self, base: str) -> str:
        base = (base or "SIMULATOR").strip().strip("/")
        max_length = Simulator._meta.get_field("cp_path").max_length
        base = base[:max_length]
        candidate = base or "SIMULATOR"
        counter = 2
        while Simulator.objects.filter(cp_path__iexact=candidate).exists():
            suffix = f"-sim{counter}"
            candidate = self._trim_with_suffix(base or "SIMULATOR", suffix, max_length=max_length)
            counter += 1
        return candidate

    def _simulator_configuration(self, charger: Charger) -> ChargerConfiguration | None:
        if charger.configuration_id:
            return charger.configuration
        return None

    def _create_simulator_from_charger(self, charger: Charger) -> Simulator:
        name = self._unique_simulator_name(self._simulator_base_name(charger))
        cp_path_base = self._simulator_cp_path_base(charger)
        cp_path = self._unique_simulator_cp_path(cp_path_base)
        connector_id = charger.connector_id if charger.connector_id is not None else 1
        simulator = Simulator.objects.create(
            name=name,
            cp_path=cp_path,
            serial_number=charger.charger_id,
            connector_id=connector_id,
            configuration=self._simulator_configuration(charger),
        )
        return simulator

    def _report_simulator_error(self, request, charger: Charger, error: Exception) -> None:
        if isinstance(error, ValidationError):
            messages_list: list[str] = []
            if getattr(error, "message_dict", None):
                for field_errors in error.message_dict.values():
                    messages_list.extend(str(item) for item in field_errors)
            elif getattr(error, "messages", None):
                messages_list.extend(str(item) for item in error.messages)
            else:
                messages_list.append(str(error))
        else:
            messages_list = [str(error)]

        charger_name = self._charger_display_name(charger)
        for message_text in messages_list:
            self.message_user(
                request,
                _("Unable to create simulator for %(charger)s: %(error)s")
                % {"charger": charger_name, "error": message_text},
                level=messages.ERROR,
            )

    @admin.action(description=_("Create Simulator for CPs"))
    def create_simulator_for_cp(self, request, queryset):
        created: list[tuple[Charger, Simulator]] = []
        for charger in queryset:
            try:
                simulator = self._create_simulator_from_charger(charger)
            except Exception as exc:  # pragma: no cover - defensive
                self._report_simulator_error(request, charger, exc)
            else:
                created.append((charger, simulator))

        if not created:
            self.message_user(
                request,
                _("No simulators were created."),
                level=messages.WARNING,
            )
            return None

        first_charger, first_simulator = created[0]
        first_label = self._charger_display_name(first_charger)
        change_url = reverse("admin:ocpp_simulator_change", args=[first_simulator.pk])
        link = format_html('<a href="{}">{}</a>', change_url, first_simulator.name)
        total = len(created)
        message = format_html(
            ngettext(
                "Created {count} simulator for the selected charge point. First simulator: {simulator}.",
                "Created {count} simulators for the selected charge points. First simulator: {simulator}.",
                total,
            ),
            count=total,
            simulator=link,
        )
        self.message_user(request, message, level=messages.SUCCESS)
        if total == 1:
            detail_message = format_html(
                _("Configured for {charger_name}."),
                charger_name=first_label,
            )
            self.message_user(request, detail_message)
        return HttpResponseRedirect(change_url)
