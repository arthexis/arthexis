from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4
from zipfile import BadZipFile

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

from .forms import CodexSkillPackageImportForm
from .models import AgentSkill, AgentSkillFile
from .package_services import (
    CodexSkillPackageImportError,
    import_codex_skill_package,
)

_SESSION_IMPORT_PACKAGES_KEY = "skills_agentskill_import_packages"
_IMPORT_UPLOAD_STORAGE_DIR = "skills/imports"
_IMPORT_UPLOAD_PREFIX = "agentskill-package-"
_IMPORT_PREVIEW_TTL_SECONDS = 60 * 60
_PACKAGE_IMPORT_ERRORS = (
    BadZipFile,
    json.JSONDecodeError,
    UnicodeDecodeError,
    CodexSkillPackageImportError,
)


class AgentSkillFileInline(admin.TabularInline):
    model = AgentSkillFile
    extra = 0
    fields = (
        "relative_path",
        "portability",
        "included_by_default",
        "exclusion_reason",
        "size_bytes",
    )
    readonly_fields = ("size_bytes",)


@admin.register(AgentSkill)
class AgentSkillAdmin(admin.ModelAdmin):
    list_display = ("slug", "title")
    search_fields = ("slug", "title", "markdown")
    filter_horizontal = ("node_roles",)
    inlines = (AgentSkillFileInline,)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "import-package/",
                self.admin_site.admin_view(self.import_package_view),
                name="skills_agentskill_import_package",
            ),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if self.has_import_package_permission(request):
            extra_context["model_import_url"] = reverse(
                "admin:skills_agentskill_import_package",
            )
        return super().changelist_view(request, extra_context=extra_context)

    def has_import_package_permission(self, request: HttpRequest) -> bool:
        file_opts = AgentSkillFile._meta
        required_file_perms = [
            f"{file_opts.app_label}.{action}_{file_opts.model_name}"
            for action in ("add", "change", "delete")
        ]
        return (
            self.has_add_permission(request)
            and self.has_change_permission(request)
            and request.user.has_perms(required_file_perms)
        )

    def import_package_view(self, request: HttpRequest):
        if not self.has_import_package_permission(request):
            raise PermissionDenied

        if request.method == "POST" and request.POST.get("action") == "apply":
            return self._apply_import_package(request)

        self._cleanup_expired_import_uploads()
        form = CodexSkillPackageImportForm()
        preview = None
        preview_token = ""
        if request.method == "POST":
            form = CodexSkillPackageImportForm(request.POST, request.FILES)
            if form.is_valid():
                upload_name = self._store_import_upload(form.cleaned_data["package"])
                try:
                    with default_storage.open(upload_name, "rb") as upload_file:
                        preview = import_codex_skill_package(upload_file, dry_run=True)
                except _PACKAGE_IMPORT_ERRORS as error:
                    self._delete_import_upload(upload_name)
                    self.message_user(
                        request,
                        _("Could not preview Codex skill package: %(error)s")
                        % {"error": error},
                        level=messages.ERROR,
                    )
                else:
                    preview_token = self._remember_import_package(
                        request,
                        upload_name,
                    )
                    self.message_user(
                        request,
                        self._summary_message(
                            preview,
                            singular=_(
                                "Previewed %(skill_count)d skill with %(file_count)d file."
                            ),
                            plural=_(
                                "Previewed %(skill_count)d skills with %(file_count)d files."
                            ),
                        ),
                        level=messages.SUCCESS,
                    )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Import Codex skill package"),
            "form": form,
            "preview": preview,
            "preview_token": preview_token,
            "changelist_url": reverse("admin:skills_agentskill_changelist"),
        }
        return TemplateResponse(
            request,
            "admin/skills/agentskill/import_package.html",
            context,
        )

    def _apply_import_package(self, request: HttpRequest) -> HttpResponseRedirect:
        package_path = self._consume_import_package(
            request,
            request.POST.get("token", ""),
        )
        import_url = reverse("admin:skills_agentskill_import_package")
        if package_path is None:
            self.message_user(
                request,
                _("The package preview expired. Upload the package again."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(import_url)

        try:
            with default_storage.open(package_path, "rb") as upload_file:
                import_codex_skill_package(upload_file, dry_run=True)
            with default_storage.open(package_path, "rb") as upload_file:
                summary = import_codex_skill_package(upload_file, dry_run=False)
        except FileNotFoundError:
            self.message_user(
                request,
                _("The package preview expired. Upload the package again."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(import_url)
        except _PACKAGE_IMPORT_ERRORS as error:
            self.message_user(
                request,
                _("Could not import Codex skill package: %(error)s") % {"error": error},
                level=messages.ERROR,
            )
            return HttpResponseRedirect(import_url)
        finally:
            self._delete_import_upload(package_path)

        self.message_user(
            request,
            self._summary_message(
                summary,
                singular=_("Imported %(skill_count)d skill with %(file_count)d file."),
                plural=_("Imported %(skill_count)d skills with %(file_count)d files."),
            ),
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:skills_agentskill_changelist"))

    def _store_import_upload(self, uploaded_file) -> str:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        storage_name = (
            f"{_IMPORT_UPLOAD_STORAGE_DIR}/{_IMPORT_UPLOAD_PREFIX}{uuid4().hex}.zip"
        )
        return default_storage.save(storage_name, uploaded_file)

    def _cleanup_expired_import_uploads(self, now=None) -> None:
        now = now or timezone.now()
        cutoff = now - timedelta(seconds=_IMPORT_PREVIEW_TTL_SECONDS)
        for storage_name in self._iter_import_upload_names():
            try:
                modified_at = default_storage.get_modified_time(storage_name)
            except (AttributeError, FileNotFoundError, NotImplementedError, OSError):
                continue
            if timezone.is_naive(cutoff) and timezone.is_aware(modified_at):
                modified_at = timezone.make_naive(
                    modified_at,
                    timezone.get_current_timezone(),
                )
            elif timezone.is_aware(cutoff) and timezone.is_naive(modified_at):
                modified_at = timezone.make_aware(
                    modified_at,
                    timezone.get_current_timezone(),
                )
            if modified_at < cutoff:
                self._delete_import_upload(storage_name)

    def _iter_import_upload_names(self) -> list[str]:
        try:
            _, filenames = default_storage.listdir(_IMPORT_UPLOAD_STORAGE_DIR)
        except (FileNotFoundError, NotImplementedError, OSError):
            return []
        return [
            f"{_IMPORT_UPLOAD_STORAGE_DIR}/{filename}"
            for filename in filenames
            if filename.startswith(_IMPORT_UPLOAD_PREFIX) and filename.endswith(".zip")
        ]

    def _delete_import_upload(self, storage_name: str) -> None:
        if not storage_name:
            return
        try:
            default_storage.delete(storage_name)
        except (FileNotFoundError, NotImplementedError, OSError):
            return

    def _remember_import_package(
        self,
        request: HttpRequest,
        storage_name: str,
    ) -> str:
        now = timezone.now()
        self._cleanup_expired_import_uploads(now=now)
        packages = dict(request.session.get(_SESSION_IMPORT_PACKAGES_KEY, {}))
        for existing_entry in packages.values():
            existing_name = self._import_package_entry_name(existing_entry)
            self._delete_import_upload(existing_name)
        packages = {}
        token = uuid4().hex
        packages[token] = {"name": storage_name, "ts": now.timestamp()}
        request.session[_SESSION_IMPORT_PACKAGES_KEY] = packages
        request.session.modified = True
        return token

    def _import_package_entry_name(self, entry) -> str:
        if isinstance(entry, dict):
            return str(entry.get("name") or "")
        return str(entry or "")

    def _import_package_entry_expired(self, entry, *, now=None) -> bool:
        if not isinstance(entry, dict):
            return True
        try:
            previewed_at = float(entry.get("ts"))
        except (TypeError, ValueError):
            return True
        now = now or timezone.now()
        return previewed_at < now.timestamp() - _IMPORT_PREVIEW_TTL_SECONDS

    def _consume_import_package(
        self,
        request: HttpRequest,
        token: str,
    ) -> str | None:
        packages = dict(request.session.get(_SESSION_IMPORT_PACKAGES_KEY, {}))
        entry = packages.pop(token, None)
        request.session[_SESSION_IMPORT_PACKAGES_KEY] = packages
        request.session.modified = True
        storage_name = self._import_package_entry_name(entry)
        if not storage_name:
            return None
        if self._import_package_entry_expired(entry):
            self._delete_import_upload(storage_name)
            return None
        try:
            exists = default_storage.exists(storage_name)
        except (NotImplementedError, OSError):
            exists = False
        if not exists:
            return None
        return storage_name

    def _summary_message(self, summary: dict, *, singular, plural) -> str:
        skills = summary.get("skills", [])
        skill_count = len(skills)
        file_count = sum(int(skill.get("files", 0)) for skill in skills)
        return ngettext(singular, plural, skill_count) % {
            "skill_count": skill_count,
            "file_count": file_count,
        }


@admin.register(AgentSkillFile)
class AgentSkillFileAdmin(admin.ModelAdmin):
    list_display = (
        "skill",
        "relative_path",
        "portability",
        "included_by_default",
        "size_bytes",
    )
    list_filter = ("portability", "included_by_default")
    search_fields = ("skill__slug", "relative_path", "exclusion_reason", "content")
