from __future__ import annotations

import json
from pathlib import Path

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import LogbookEntry


class LogbookEntryForm(forms.ModelForm):
    class MultiFileInput(forms.ClearableFileInput):
        allow_multiple_selected = True

    images = forms.Field(
        required=False,
        widget=MultiFileInput(attrs={"multiple": True}),
        help_text=_("Attach one or more images"),
    )
    logs = forms.MultipleChoiceField(
        required=False,
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Select log files to include"),
        label=_("Logs"),
    )
    debug_document = forms.FileField(
        required=False,
        help_text=_("Optional JSON document with debug info"),
        label=_("Debug JSON document"),
    )

    class Meta:
        model = LogbookEntry
        fields = ["title", "report", "event_at", "debug_info"]
        widgets = {
            "event_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        help_texts = {
            "debug_info": _("Provide structured debug data as JSON"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logs_dir = Path(getattr(settings, "LOG_DIR", settings.BASE_DIR / "logs"))
        choices = []
        if logs_dir.exists() and logs_dir.is_dir():
            choices = [
                (entry.name, entry.name)
                for entry in sorted(
                    logs_dir.iterdir(), key=lambda item: item.name.lower()
                )
                if entry.is_file() and not entry.name.startswith(".")
            ][:100]
        self.fields["logs"].choices = choices

    def clean_debug_info(self):
        data = self.cleaned_data.get("debug_info")
        if isinstance(data, str) and data.strip():
            try:
                return json.loads(data)
            except json.JSONDecodeError as exc:
                raise forms.ValidationError(_("Invalid JSON: %(error)s") % {"error": exc})
        return data

    def clean_debug_document(self):
        document = self.cleaned_data.get("debug_document")
        if not document:
            return None
        try:
            json.loads(document.read().decode("utf-8"))
        except Exception as exc:
            raise forms.ValidationError(_("Debug document must contain valid JSON (%(error)s)"))
        finally:
            if document and hasattr(document, "seek"):
                document.seek(0)
        return document

    def clean_images(self):
        return self.files.getlist("images") if hasattr(self, "files") else []
