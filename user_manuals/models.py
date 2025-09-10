from django.db import models

class UserManual(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    content_html = models.TextField()
    content_pdf = models.TextField(help_text="Base64 encoded PDF")

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "User Manual"
        verbose_name_plural = "User Manuals"
