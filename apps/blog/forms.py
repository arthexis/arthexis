"""Forms for blog user interactions."""

from django import forms

from apps.blog.models import BlogComment


class BlogCommentForm(forms.ModelForm):
    """Capture and validate a public comment for a blog post."""

    class Meta:
        model = BlogComment
        fields = ["author_name", "author_email", "body"]
