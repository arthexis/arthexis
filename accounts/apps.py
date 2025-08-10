from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = _("Business Models")

    def ready(self):  # pragma: no cover - called by Django
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_migrate

        def create_default_admin(**kwargs):
            User = get_user_model()
            if not User.objects.exists():
                User.objects.create_superuser("admin", password="admin")

        post_migrate.connect(create_default_admin, sender=self)
