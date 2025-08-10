from celery import shared_task
from post_office.mail import send_queued_mail_until_done


@shared_task
def send_pending_emails() -> None:
    """Send any queued emails using django-post_office."""
    send_queued_mail_until_done()
