"""Diagnostics helpers and actions for charger admin."""

from ..common_imports import *


class ChargerDiagnosticsMixin:
    """Provide diagnostics setup, request, and retrieval actions."""

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
