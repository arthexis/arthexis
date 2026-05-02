from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from uuid import uuid4
from zipfile import BadZipFile

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _, ngettext

from .forms import CodexSkillPackageImportForm
from .models import AgentSkill, AgentSkillFile
from .package_services import (
    CodexSkillPackageImportError,
    import_codex_skill_package,
)

_SESSION_IMPORT_PACKAGES_KEY = "skills_agentskill_import_packages"
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
        return self.has_add_permission(request) and self.has_change_permission(request)

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
                upload_path = self._store_import_upload(form.cleaned_data["package"])
                try:
                    preview = import_codex_skill_package(upload_path, dry_run=True)
                except _PACKAGE_IMPORT_ERRORS as error:
                    upload_path.unlink(missing_ok=True)
                    self.message_user(
                        request,
                        _("Could not preview Codex skill package: %(error)s")
                        % {"error": error},
                        level=messages.ERROR,
                    )
                else:
                    preview_token = self._remember_import_package(
                        request,
                        upload_path,
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
            import_codex_skill_package(package_path, dry_run=True)
            summary = import_codex_skill_package(package_path, dry_run=False)
        except _PACKAGE_IMPORT_ERRORS as error:
            self.message_user(
                request,
                _("Could not import Codex skill package: %(error)s") % {"error": error},
                level=messages.ERROR,
            )
            return HttpResponseRedirect(import_url)
        finally:
            package_path.unlink(missing_ok=True)

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

    def _store_import_upload(self, uploaded_file) -> Path:
        with NamedTemporaryFile(
            delete=False,
            dir=gettempdir(),
            prefix=_IMPORT_UPLOAD_PREFIX,
            suffix=".zip",
        ) as temp_file:
            for chunk in uploaded_file.chunks():
                temp_file.write(chunk)
            return Path(temp_file.name)

    def _cleanup_expired_import_uploads(self, now: float | None = None) -> None:
        now = now or time.time()
        cutoff = now - _IMPORT_PREVIEW_TTL_SECONDS
        for package_path in Path(gettempdir()).glob(f"{_IMPORT_UPLOAD_PREFIX}*.zip"):
            try:
                if package_path.stat().st_mtime < cutoff:
                    package_path.unlink(missing_ok=True)
            except OSError:
                continue

    def _remember_import_package(
        self,
        request: HttpRequest,
        package_path: Path,
    ) -> str:
        now = time.time()
        self._cleanup_expired_import_uploads(now=now)
        packages = dict(request.session.get(_SESSION_IMPORT_PACKAGES_KEY, {}))
        for existing_entry in packages.values():
            existing_path = self._import_package_entry_path(existing_entry)
            if existing_path:
                Path(existing_path).unlink(missing_ok=True)
        packages = {}
        token = uuid4().hex
        packages[token] = {"path": str(package_path), "ts": now}
        request.session[_SESSION_IMPORT_PACKAGES_KEY] = packages
        request.session.modified = True
        return token

    def _import_package_entry_path(self, entry) -> str:
        if isinstance(entry, dict):
            return str(entry.get("path") or "")
        return str(entry or "")

    def _consume_import_package(
        self,
        request: HttpRequest,
        token: str,
    ) -> Path | None:
        packages = dict(request.session.get(_SESSION_IMPORT_PACKAGES_KEY, {}))
        entry = packages.pop(token, None)
        request.session[_SESSION_IMPORT_PACKAGES_KEY] = packages
        request.session.modified = True
        raw_path = self._import_package_entry_path(entry)
        if not raw_path:
            return None
        package_path = Path(raw_path)
        if not package_path.exists():
            return None
        return package_path

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
