from django.db import models
from django.db.models.fields import DeferredAttribute


class _BaseSigilDescriptor(DeferredAttribute):
    def __set__(self, instance, value):
        instance.__dict__[self.field.attname] = value


class _CheckSigilDescriptor(_BaseSigilDescriptor):
    def __get__(self, instance, cls=None):
        value = super().__get__(instance, cls)
        if instance is None:
            return value
        if getattr(instance, f"{self.field.name}_resolve_sigils", False):
            return instance.resolve_sigils(self.field.name)
        return value


class _AutoSigilDescriptor(_BaseSigilDescriptor):
    def __get__(self, instance, cls=None):
        value = super().__get__(instance, cls)
        if instance is None:
            return value
        return instance.resolve_sigils(self.field.name)


class _SigilBaseField:
    def value_from_object(self, obj):
        return obj.__dict__.get(self.attname)


class SigilCheckFieldMixin(_SigilBaseField):
    descriptor_class = _CheckSigilDescriptor

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)
        extra_name = f"{name}_resolve_sigils"
        if not any(f.name == extra_name for f in cls._meta.fields):
            cls.add_to_class(
                extra_name,
                models.BooleanField(
                    default=False,
                    verbose_name="Resolve [SIGILS] in templates",
                ),
            )


class SigilAutoFieldMixin(_SigilBaseField):
    descriptor_class = _AutoSigilDescriptor

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)


class SigilShortCheckField(SigilCheckFieldMixin, models.CharField):
    pass


class SigilLongCheckField(SigilCheckFieldMixin, models.TextField):
    pass


class SigilShortAutoField(SigilAutoFieldMixin, models.CharField):
    pass


class SigilLongAutoField(SigilAutoFieldMixin, models.TextField):
    pass
