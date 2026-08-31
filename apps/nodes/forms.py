from django import forms
from django.utils.translation import gettext_lazy as _

from .models import NodeRole


class NodeRoleSelectMultiple(forms.SelectMultiple):
    def __init__(self, *args, **kwargs):
        attrs = kwargs.pop("attrs", {})
        attrs.setdefault("size", 4)
        css_class = attrs.get("class", "")
        attrs["class"] = f"{css_class} node-role-multiselect".strip()
        super().__init__(attrs=attrs, *args, **kwargs)


class NodeRoleMultipleChoiceField(forms.ModelMultipleChoiceField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("queryset", NodeRole.objects.all())
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", NodeRoleSelectMultiple())
        kwargs.setdefault(
            "help_text",
            _("Leave blank to apply to all node roles."),
        )
        super().__init__(*args, **kwargs)
