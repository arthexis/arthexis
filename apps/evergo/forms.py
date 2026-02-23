"""Forms used by Evergo admin tools."""

from django import forms

from .models import EvergoUser


class EvergoLoadCustomersForm(forms.Form):
    """Collect profile and free-form SO/name input for customer sync."""

    profile = forms.ModelChoiceField(
        queryset=EvergoUser.objects.all().order_by("evergo_email", "id"),
        help_text="Profile used to authenticate against Evergo.",
    )
    raw_queries = forms.CharField(
        label="SO numbers and/or customer names",
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "style": "padding: 12px 14px; line-height: 1.5;",
            }
        ),
        help_text=(
            "Paste values separated by spaces, commas, semicolons, pipes, tabs, or new lines. "
            "SO patterns like J00830 are detected automatically."
        ),
    )

    def __init__(self, *args, request_user=None, **kwargs):
        """Optionally preselect an Evergo profile owned by the current request user."""
        super().__init__(*args, **kwargs)
        if self.is_bound or request_user is None or not request_user.is_authenticated:
            return

        owned_profile = (
            EvergoUser.objects.filter(user=request_user)
            .order_by("evergo_email", "id")
            .first()
        )
        if owned_profile:
            self.fields["profile"].initial = owned_profile.pk
