from django.conf import settings

from .models import BskyAccount


def register_account(user, handle: str, app_password: str) -> BskyAccount:
    """Link a Bluesky account to ``user``.

    The credentials are stored for later use when publishing posts.
    """

    from atproto import Client

    client = Client()
    client.login(handle, app_password)

    account, _ = BskyAccount.objects.update_or_create(
        user=user, defaults={"handle": handle, "app_password": app_password}
    )
    return account


def post_from_user(user, text: str) -> None:
    """Publish ``text`` using the Bluesky account linked to ``user``."""

    from atproto import Client

    account = BskyAccount.objects.get(user=user)
    client = Client()
    client.login(account.handle, account.app_password)
    client.send_post(text)


def post_from_domain(text: str) -> None:
    """Publish ``text`` using the domain-wide Bluesky account."""

    from atproto import Client

    handle = getattr(settings, "BSKY_HANDLE", None)
    password = getattr(settings, "BSKY_APP_PASSWORD", None)
    if not handle or not password:
        raise ValueError("Domain Bluesky account is not configured")

    client = Client()
    client.login(handle, password)
    client.send_post(text)
