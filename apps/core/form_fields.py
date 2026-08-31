"""Custom form fields for the Arthexis admin."""

from __future__ import annotations

import base64
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory
from django.forms.fields import FileField
from django.forms.widgets import FILE_INPUT_CONTRADICTION, Select, TextInput
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


class SchedulePeriodForm(forms.Form):
    start_period = forms.IntegerField(
        min_value=0,
        label=_("Start period (seconds)"),
        widget=TextInput(attrs={"size": 10}),
    )
    limit = forms.DecimalField(
        max_digits=10,
        decimal_places=3,
        label=_("Limit"),
        widget=TextInput(attrs={"size": 10}),
    )
    number_phases = forms.TypedChoiceField(
        required=False,
        label=_("Number of phases"),
        coerce=int,
        empty_value=None,
        choices=(
            ("", _("Use charger default")),
            (1, _("1-phase")),
            (3, _("3-phase")),
        ),
        widget=Select(attrs={"class": "vTextField"}),
    )
    phase_to_use = forms.IntegerField(
        min_value=1,
        required=False,
        label=_("Phase to use"),
        widget=TextInput(attrs={"size": 6}),
    )

    def clean(self):
        cleaned = super().clean()
        phase_to_use = cleaned.get("phase_to_use")
        number_phases = cleaned.get("number_phases")
        if phase_to_use and not number_phases:
            self.add_error(
                "number_phases",
                _("Specify the number of phases when selecting a phase to use."),
            )
        return cleaned


class SchedulePeriodsWidget(forms.Widget):
    template_name = "admin/includes/celery/schedule_periods.html"

    def __init__(self, *, formset_class: type[BaseFormSet], attrs=None):
        self.formset_class = formset_class
        super().__init__(attrs)

    def value_from_datadict(self, data, files, name):
        formset = self.formset_class(data=data, files=files, prefix=name)
        formset.full_clean()
        return formset

    def get_context(self, name, value, attrs):
        value = value or []
        context = super().get_context(name, value, attrs)
        if isinstance(value, BaseFormSet):
            formset = value
        else:
            initial_periods = value if isinstance(value, list) else []
            if not initial_periods:
                initial_periods = [{}]
            formset = self.formset_class(initial=initial_periods, prefix=name)
        context["widget"]["formset"] = formset
        context["widget"]["name"] = name
        return context

    @property
    def media(self):  # pragma: no cover - renders within admin form media
        return forms.Media(js=["admin/js/jquery.init.js"])


class SchedulePeriodsField(forms.Field):
    default_error_messages = {
        "invalid": _("Add at least one schedule period."),
    }

    def __init__(self, *args, **kwargs):
        formset_class = formset_factory(
            SchedulePeriodForm, extra=0, can_delete=True, validate_min=True
        )
        widget = kwargs.pop(
            "widget", SchedulePeriodsWidget(formset_class=formset_class)
        )
        super().__init__(*args, widget=widget, **kwargs)
        self.formset_class = formset_class

    def prepare_value(self, value):
        return value or []

    def clean(self, value: Any):
        if isinstance(value, BaseFormSet):
            if not value.is_valid():
                errors: list[str] = []
                errors.extend(str(error) for error in value.non_form_errors())
                for form in value.forms:
                    for field, field_errors in form.errors.items():
                        for error in field_errors:
                            errors.append(f"{field}: {error}")
                raise ValidationError(errors or self.error_messages["invalid"])

            periods: list[dict[str, object]] = []
            for form in value.forms:
                cleaned = getattr(form, "cleaned_data", {}) or {}
                if cleaned.get("DELETE"):
                    continue
                periods.append(
                    {
                        "start_period": cleaned.get("start_period"),
                        "limit": cleaned.get("limit"),
                        "number_phases": cleaned.get("number_phases"),
                        "phase_to_use": cleaned.get("phase_to_use"),
                    }
                )
            if not periods:
                raise ValidationError(self.error_messages["invalid"])
            return periods
        return super().clean(value)
