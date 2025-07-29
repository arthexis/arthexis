from django.db import models
from django.utils import timezone


class EmailTemplate(models.Model):
    """Simple email template with subject and body."""

    name = models.CharField(max_length=100, unique=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()

    def __str__(self):
        return self.name


class QueuedEmail(models.Model):
    """Email to be sent later."""

    to = models.EmailField()
    template = models.ForeignKey(EmailTemplate, on_delete=models.CASCADE)
    context = models.JSONField(blank=True, default=dict)
    sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)

    def mark_sent(self):
        self.sent = True
        self.sent_at = timezone.now()
        self.save(update_fields=["sent", "sent_at"])
