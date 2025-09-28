"""Custom form fields for the Arthexis admin."""

from __future__ import annotations

import base64
from typing import Any

from django.core.exceptions import ValidationError
from django.forms.fields import FileField
from django.forms.widgets import FILE_INPUT_CONTRADICTION
from django.utils.translation import gettext_lazy as _

from .widgets import AdminBase64FileWidget


class Base64FileField(FileField):
    """Form field storing uploaded files as base64 encoded strings.

    The field behaves like :class:`~django.forms.FileField` from the user's
    perspective. Uploaded files are converted to base64 and returned as text so
    they can be stored in ``TextField`` columns. When no new file is uploaded the
    initial base64 value is preserved, while clearing the field stores an empty
    string.
    """

    widget = AdminBase64FileWidget
    default_error_messages = {
        **FileField.default_error_messages,
        "contradiction": _(
            "Please either submit a file or check the clear checkbox, not both."
        ),
    }

    def __init__(
        self,
        *,
        download_name: str | None = None,
        content_type: str = "application/octet-stream",
        **kwargs: Any,
    ) -> None:
        widget = kwargs.pop("widget", None) or self.widget()
        if download_name:
            widget.download_name = download_name
        if content_type:
            widget.content_type = content_type
        super().__init__(widget=widget, **kwargs)

    def to_python(self, data: Any) -> str | None:
        """Convert uploaded data to a base64 string."""

        if isinstance(data, str):
            return data
        uploaded = super().to_python(data)
        if uploaded is None:
            return None
        content = uploaded.read()
        if hasattr(uploaded, "seek"):
            uploaded.seek(0)
        return base64.b64encode(content).decode("ascii")

    def clean(self, data: Any, initial: str | None = None) -> str:
        if data is FILE_INPUT_CONTRADICTION:
            raise ValidationError(
                self.error_messages["contradiction"], code="contradiction"
            )
        cleaned = super().clean(data, initial)
        if cleaned in {None, False}:
            return ""
        return cleaned

    def bound_data(self, data: Any, initial: str | None) -> str | None:
        return initial

    def has_changed(self, initial: str | None, data: Any) -> bool:
        return not self.disabled and data is not None
