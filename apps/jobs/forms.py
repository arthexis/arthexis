from django import forms

from .models import CVSubmission


class CVSubmissionForm(forms.ModelForm):
    """Collect CV files and candidate details from the public jobs page."""

    class Meta:
        model = CVSubmission
        fields = [
            "job_posting",
            "full_name",
            "email",
            "phone",
            "cv_file",
            "cover_letter",
            "notes",
        ]
        widgets = {
            "job_posting": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "cv_file": forms.FileInput(attrs={"class": "form-control"}),
            "cover_letter": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        posting_queryset = kwargs.pop("posting_queryset")
        super().__init__(*args, **kwargs)
        self.fields["job_posting"].queryset = posting_queryset
        self.fields["job_posting"].required = False
