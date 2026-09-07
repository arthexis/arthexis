from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    verbose_name = "Users"

    def ready(self):
        from django.core.signals import got_request_exception

        from .diagnostics import attach_exception_signal

        got_request_exception.connect(
            attach_exception_signal,
            dispatch_uid="users.diagnostics.request_exception",
        )
