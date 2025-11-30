import json
from django.contrib.admin.options import ModelAdmin
from django.contrib.admin.models import LogEntry
from django.utils.encoding import smart_str


def patch_admin_history():
    if getattr(ModelAdmin, "_history_patched", False):
        return

    def construct_change_message(self, request, form, formsets, add=False):
        fields = []
        if add:
            for name, value in form.cleaned_data.items():
                fields.append({"field": name, "old": None, "new": smart_str(value)})
            if not fields:
                return ""
            return json.dumps({"added": fields})
        for name in form.changed_data:
            fields.append(
                {
                    "field": name,
                    "old": smart_str(form.initial.get(name)),
                    "new": smart_str(form.cleaned_data.get(name)),
                }
            )
        if not fields:
            return ""
        return json.dumps({"changed": fields})

    def get_change_message(self):
        try:
            data = json.loads(self.change_message)
        except Exception:
            return self.change_message
        if isinstance(data, dict):
            if "added" in data:
                parts = [f"{d['field']}='{d['new']}'" for d in data["added"]]
                return "Added " + ", ".join(parts)
            if "changed" in data:
                parts = [
                    f"{d['field']}: '{d['old']}' -> '{d['new']}'"
                    for d in data["changed"]
                ]
                return "Changed " + ", ".join(parts)
        return self.change_message

    ModelAdmin.construct_change_message = construct_change_message
    LogEntry.get_change_message = get_change_message
    ModelAdmin._history_patched = True
