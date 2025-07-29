from django.contrib import admin
from .models import EmailTemplate, QueuedEmail

admin.site.register(EmailTemplate)
admin.site.register(QueuedEmail)
