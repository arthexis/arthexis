from django.db import models


class EmailTemplate(models.Model):
    """Simple email template with subject and body."""

    name = models.CharField(max_length=100, unique=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()

    def __str__(self):
        return self.name
