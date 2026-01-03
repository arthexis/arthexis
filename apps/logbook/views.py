from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.content.models import ContentSample
from apps.content.utils import save_content_sample
from apps.nodes.models import NetMessage, Node, NodeRole

from .forms import LogbookEntryForm
from .models import LogbookEntry, LogbookLogAttachment


class LogbookDetailView(TemplateView):
    template_name = "logbook/detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        secret = self.kwargs.get("secret")
        entry = LogbookEntry.objects.filter(secret=secret).first()
        if not entry:
            raise Http404

        image_urls: list[str] = []
        media_root = Path(settings.MEDIA_ROOT)
        for sample in entry.content_samples.all():
            if not sample.path:
                continue
            sample_path = Path(sample.path)
            if sample_path.is_absolute() and media_root in sample_path.parents:
                relative = sample_path.relative_to(media_root)
                image_urls.append(default_storage.url(str(relative)))
            elif not sample_path.is_absolute():
                candidate = media_root / sample_path
                if candidate.exists():
                    image_urls.append(default_storage.url(str(sample_path)))

        context.update(
            {
                "entry": entry,
                "image_urls": image_urls,
                "debug_pretty": json.dumps(entry.debug_info, indent=2)
                if entry.debug_info
                else "",
            }
        )
        return context


class LogbookCreateView(LoginRequiredMixin, FormView):
    form_class = LogbookEntryForm
    template_name = "logbook/create.html"
    success_url = reverse_lazy("logbook:create")

    def form_valid(self, form):
        entry: LogbookEntry = form.save(commit=False)
        entry.user = self.request.user if self.request.user.is_authenticated else None
        event_at = form.cleaned_data.get("event_at")
        entry.event_at = event_at
        debug_info = form.cleaned_data.get("debug_info")
        entry.debug_info = debug_info
        entry.save()

        if entry.event_at is None and entry.created_at:
            entry.event_at = entry.created_at
            entry.save(update_fields=["event_at"])

        debug_document = form.cleaned_data.get("debug_document")
        if debug_document:
            try:
                entry.attach_debug_document(debug_document)
                if not entry.debug_info:
                    entry.debug_info = json.load(debug_document)
                    entry.save(update_fields=["debug_info"])
            except Exception:
                messages.warning(
                    self.request,
                    _("Unable to store debug document."),
                )

        self._persist_images(entry, self.request.FILES.getlist("images"))
        self._persist_logs(entry, form.cleaned_data.get("logs") or [])
        self._broadcast(entry)

        messages.success(
            self.request,
            _("Logbook entry created. Share the secret link with collaborators."),
        )
        return redirect(entry.get_absolute_url())

    def form_invalid(self, form):
        messages.error(self.request, _("Please correct the errors below."))
        return super().form_invalid(form)

    def _persist_images(self, entry: LogbookEntry, files):
        for uploaded in files:
            stored_name = default_storage.save(
                f"logbook/images/{entry.secret}-{uploaded.name}", uploaded
            )
            stored_path = Path(default_storage.path(stored_name))
            sample = save_content_sample(
                path=stored_path,
                kind=ContentSample.IMAGE,
                node=entry.node,
                user=entry.user,
                method="LOGBOOK",
                link_duplicates=True,
                duplicate_log_context="logbook image",
            )
            if sample is None:
                sample = ContentSample.objects.create(
                    path=str(stored_path),
                    kind=ContentSample.IMAGE,
                    node=entry.node,
                    user=entry.user,
                )
            entry.add_image_sample(sample)

    def _persist_logs(self, entry: LogbookEntry, log_names: list[str]):
        logs_dir = Path(getattr(settings, "LOG_DIR", settings.BASE_DIR / "logs"))
        for name in log_names:
            source = logs_dir / name
            if not source.exists() or not source.is_file():
                continue
            attachment = LogbookLogAttachment(
                entry=entry,
                original_name=name,
            )
            attachment.file.save(
                f"logbook/logs/{entry.secret}-{source.name}",
                ContentFile(source.read_bytes()),
                save=False,
            )
            if hasattr(default_storage, "path"):
                stored_path = Path(default_storage.path(attachment.file.name))
                attachment.size = stored_path.stat().st_size if stored_path.exists() else 0
            attachment.save()

    def _broadcast(self, entry: LogbookEntry) -> None:
        local_node = entry.node or Node.get_local()
        node_label = getattr(local_node, "name", None) or getattr(
            local_node, "hostname", "node"
        )
        reach = (
            NodeRole.objects.filter(name__in=["Watchtower", "Constellation"])
            .order_by("name")
            .first()
        )
        message = NetMessage.broadcast(
            subject=f"FROM {node_label}",
            body=entry.secret,
            reach=reach,
        )
        target_limit = Node.objects.count()
        if target_limit and message.target_limit != target_limit:
            message.target_limit = target_limit
            message.save(update_fields=["target_limit"])
            message.propagate()
