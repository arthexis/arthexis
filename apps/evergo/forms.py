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
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text=(
            "Paste values separated by spaces, commas, semicolons, pipes, tabs, or new lines. "
            "SO patterns like J00830 are detected automatically."
        ),
    )
