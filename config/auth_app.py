from django.contrib.auth.apps import AuthConfig as DjangoAuthConfig


class AuthConfig(DjangoAuthConfig):
    """Use a shorter label for the auth section in the admin."""

    verbose_name = "AUTH"
