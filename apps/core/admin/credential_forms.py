from django import forms


def _raw_instance_value(instance, field_name):
    """Return the stored value for ``field_name`` without resolving sigils."""

    field = instance._meta.get_field(field_name)
    if not instance.pk:
        return field.value_from_object(instance)
    manager = type(instance)._default_manager
    try:
        return (
            manager.filter(pk=instance.pk).values_list(field.attname, flat=True).get()
        )
    except type(instance).DoesNotExist:  # pragma: no cover - instance deleted
        return field.value_from_object(instance)


class KeepExistingValue:
    """Sentinel indicating a field should retain its stored value."""

    __slots__ = ("field",)

    def __init__(self, field: str):
        self.field = field

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return False

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<KeepExistingValue field={self.field!r}>"


def keep_existing(field: str) -> KeepExistingValue:
    return KeepExistingValue(field)


def _restore_sigil_values(form, field_names):
    """Reset sigil fields on ``form.instance`` to their raw form values."""

    for name in field_names:
        if name not in form.fields:
            continue
        if name in form.cleaned_data:
            raw = form.cleaned_data[name]
            if isinstance(raw, KeepExistingValue):
                raw = _raw_instance_value(form.instance, name)
        else:
            raw = _raw_instance_value(form.instance, name)
        setattr(form.instance, name, raw)


class MaskedPasswordFormMixin:
    """Mixin that hides stored passwords while allowing updates."""

    password_field_name = "password"
    password_field_render_value: bool | None = None
    password_sigil_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get(self.password_field_name)
        if field is None:
            return
        render_value = self.password_field_render_value
        if not isinstance(field.widget, forms.PasswordInput):
            field.widget = forms.PasswordInput(render_value=bool(render_value))
        elif render_value is not None:
            field.widget.render_value = render_value
        field.widget.attrs.setdefault("autocomplete", "new-password")
        field.help_text = field.help_text or "Leave blank to keep the current password."
        if self.instance.pk:
            field.required = False
            field.initial = ""
            self.initial[self.password_field_name] = ""
        else:
            field.required = True

    def _clean_password_field(self, cleaned_data):
        field = self.fields.get(self.password_field_name)
        if field is None:
            return cleaned_data
        pwd = cleaned_data.get(self.password_field_name)
        if not pwd and self.instance.pk:
            cleaned_data[self.password_field_name] = keep_existing(
                self.password_field_name
            )
        return cleaned_data

    def clean_password(self):
        cleaned_data = self._clean_password_field(self.cleaned_data)
        return cleaned_data.get(self.password_field_name)

    def clean(self):
        cleaned_data = super().clean()
        return self._clean_password_field(cleaned_data)

    def _post_clean(self):
        super()._post_clean()
        if self.password_sigil_fields:
            _restore_sigil_values(self, self.password_sigil_fields)
