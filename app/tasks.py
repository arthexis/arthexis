"""Example Celery tasks."""

import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from nodes.models import NetMessage

logger = logging.getLogger(__name__)


@shared_task
def heartbeat() -> None:
    """Log a simple heartbeat message."""
    logger.info("Heartbeat task executed")


@shared_task
def birthday_greetings() -> None:
    """Send birthday greetings to users via Net Message and email."""
    User = get_user_model()
    today = timezone.localdate()
    for user in User.objects.filter(birthday=today):
        NetMessage.broadcast("Happy bday!", user.username)
        if user.email:
            send_mail(
                "Happy bday!",
                f"Happy bday! {user.username}",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=True,
            )
