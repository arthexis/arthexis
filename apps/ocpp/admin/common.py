from .common_imports import (
    FileResponse,
    TemplateResponse,
    admin,
    messages,
    path,
    redirect,
    reverse,
    store,
)


class LogViewAdminMixin:
    """Mixin providing an admin view to display charger or simulator logs."""

    log_type = "charger"
    log_template_name = "admin/ocpp/log_view.html"

    def get_log_identifier(self, obj):  # pragma: no cover - mixin hook
        raise NotImplementedError

    def get_log_title(self, obj):
        return f"Log for {obj}"

    def get_urls(self):
        urls = super().get_urls()
        info = self.model._meta.app_label, self.model._meta.model_name
        custom = [
            path(
                "<path:object_id>/log/",
                self.admin_site.admin_view(self.log_view),
                name=f"{info[0]}_{info[1]}_log",
            ),
        ]
        return custom + urls

    def log_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            info = self.model._meta.app_label, self.model._meta.model_name
            changelist_url = reverse(
                "admin:%s_%s_changelist" % info,
                current_app=self.admin_site.name,
            )
            self.message_user(request, "Log is not available.", messages.ERROR)
            return redirect(changelist_url)
        identifier = self.get_log_identifier(obj)
        log_file = store.resolve_log_path(identifier, log_type=self.log_type)
        if log_file is None:
            log_file = store._file_path(identifier, log_type=self.log_type)
        if request.GET.get("download") == "1":
            if log_file.exists():
                response = FileResponse(
                    log_file.open("rb"),
                    as_attachment=True,
                    filename=log_file.name,
                )
                response["Content-Type"] = "text/plain; charset=utf-8"
                return response
            self.message_user(
                request,
                "Log file is not available for download.",
                messages.ERROR,
            )
            info = self.model._meta.app_label, self.model._meta.model_name
            changelist_url = reverse(
                "admin:%s_%s_changelist" % info,
                current_app=self.admin_site.name,
            )
            return redirect(changelist_url)
        allowed_limits = {"20", "40", "80", "100"}
        log_limit = request.GET.get("limit") or "40"
        if log_limit not in allowed_limits:
            log_limit = "40"
        log_entries = store.get_logs(identifier, log_type=self.log_type, limit=log_limit)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": obj,
            "title": self.get_log_title(obj),
            "log_entries": log_entries,
            "log_file": str(log_file),
            "log_identifier": identifier,
            "log_limit": log_limit,
        }
        return TemplateResponse(request, self.log_template_name, context)
