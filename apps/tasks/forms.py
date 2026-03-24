from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.ocpp.models.location import Location
from apps.media.models import MediaFile
from apps.media.utils import create_media_file
from apps.tasks.models import (
    ChargerVendorSubmission,
    ManualTaskRequest,
    TaskCategory,
    get_task_category_bucket,
)


class TaskCategoryAdminForm(forms.ModelForm):
    image_upload = forms.ImageField(
        required=False,
        label=_("Image upload"),
        help_text=_("Upload an image to represent this task category."),
    )

    class Meta:
        """Configure the task category admin form to expose every model field."""

        model = TaskCategory
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bucket = get_task_category_bucket()
        self.fields["image_media"].queryset = MediaFile.objects.filter(bucket=bucket)

    def save(self, commit=True):
        instance = super().save(commit=False)
        upload = self.cleaned_data.get("image_upload")
        if upload:
            bucket = get_task_category_bucket()
            instance.image_media = create_media_file(
                bucket=bucket, uploaded_file=upload
            )
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_image_upload(self):
        upload = self.cleaned_data.get("image_upload")
        if upload:
            bucket = get_task_category_bucket()
            if not bucket.allows_filename(upload.name):
                raise forms.ValidationError(_("File type is not allowed."))
            if not bucket.allows_size(upload.size):
                raise forms.ValidationError(_("File exceeds the allowed size."))
        return upload


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
        """Configure editable maintenance request fields and Bootstrap-oriented widgets."""

        model = ManualTaskRequest
        fields = [
            "category",
            "description",
            "duration",
            "location",
            "scheduled_start",
            "scheduled_end",
            "is_periodic",
            "period",
            "period_deadline",
            "enable_notifications",
            "github_issue_template",
            "github_issue_trigger",
            "github_issue_overdue_after",
        ]
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
            "duration": forms.TextInput(attrs={"class": "form-control"}),
            "location": forms.Select(attrs={"class": "form-select"}),
            "enable_notifications": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "is_periodic": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "period": forms.TextInput(attrs={"class": "form-control"}),
            "period_deadline": forms.TextInput(attrs={"class": "form-control"}),
            "github_issue_template": forms.Select(attrs={"class": "form-select"}),
            "github_issue_trigger": forms.Select(attrs={"class": "form-select"}),
            "github_issue_overdue_after": forms.TextInput(
                attrs={"class": "form-control"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].required = True
        self.fields["location"].queryset = Location.objects.order_by("name")
        self.fields["category"].required = True
        self.fields["category"].empty_label = None
        self.fields["category"].queryset = TaskCategory.objects.exclude(
            name=""
        ).order_by("name")
        self.fields["description"].label = _("Requestor Comments")
        self.fields["github_issue_template"].required = False
        self.fields["github_issue_template"].empty_label = _("No GitHub issue")
        self.fields["github_issue_trigger"].required = False
        self.fields["scheduled_start"].widget.attrs.setdefault("class", "form-control")
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
            raise forms.ValidationError(
                _("Select a location for this maintenance request.")
            )
        return location


class ChargerVendorSubmissionForm(forms.ModelForm):
    """Collect charger vendor details needed for integration evaluation."""

    class Meta:
        """Bind the public intake form to charger vendor submissions and their widgets."""

        model = ChargerVendorSubmission
        fields = [
            "company_name",
            "contact_name",
            "contact_email",
            "contact_phone",
            "website",
            "charger_brand",
            "charger_models",
            "ocpp_versions",
            "connectivity_summary",
            "api_documentation_url",
            "certification_summary",
            "deployment_regions",
            "deployment_volume",
            "remote_access_method",
            "hardware_notes",
            "integration_goals",
            "additional_notes",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"class": "form-control"}),
            "contact_name": forms.TextInput(attrs={"class": "form-control"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            "contact_phone": forms.TextInput(attrs={"class": "form-control"}),
            "website": forms.URLInput(attrs={"class": "form-control"}),
            "charger_brand": forms.TextInput(attrs={"class": "form-control"}),
            "charger_models": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "ocpp_versions": forms.TextInput(attrs={"class": "form-control"}),
            "connectivity_summary": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "api_documentation_url": forms.URLInput(attrs={"class": "form-control"}),
            "certification_summary": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "deployment_regions": forms.TextInput(attrs={"class": "form-control"}),
            "deployment_volume": forms.TextInput(attrs={"class": "form-control"}),
            "remote_access_method": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "hardware_notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "integration_goals": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "additional_notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def clean_ocpp_versions(self) -> str:
        """Normalize OCPP version input for consistent admin review."""

        return " ".join((self.cleaned_data.get("ocpp_versions") or "").split())
