from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.energy.models import Location
from apps.teams.models import ManualTask


class MaintenanceRequestForm(forms.ModelForm):
    """Collect maintenance task details for a specific location."""

    scheduled_start = forms.DateTimeField(
        label=_("Scheduled start"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    scheduled_end = forms.DateTimeField(
        label=_("Scheduled end"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    class Meta:
        model = ManualTask
        fields = [
            "category",
            "description",
            "duration",
            "location",
            "scheduled_start",
            "scheduled_end",
            "enable_notifications",
        ]
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(
                attrs={"rows": 4, "class": "form-control"}
            ),
            "duration": forms.TextInput(attrs={"class": "form-control"}),
            "location": forms.Select(attrs={"class": "form-select"}),
            "enable_notifications": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].required = True
        self.fields["location"].queryset = Location.objects.order_by("name")
        self.fields["category"].required = True
        self.fields["description"].label = _("Requestor Comments")
        self.fields["scheduled_start"].widget.attrs.setdefault(
            "class", "form-control"
        )
        self.fields["scheduled_end"].widget.attrs.setdefault("class", "form-control")
        if not self.is_bound:
            locations = self.fields["location"].queryset
            if locations.count() == 1:
                self.initial.setdefault("location", locations.first())
        if not self.initial.get("scheduled_start"):
            now = timezone.localtime()
            self.initial.setdefault("scheduled_start", now)
            self.initial.setdefault("scheduled_end", now + timedelta(hours=1))

    def clean_location(self):
        location = self.cleaned_data.get("location")
        if location is None:
            raise forms.ValidationError(_("Select a location for this maintenance request."))
        return location
