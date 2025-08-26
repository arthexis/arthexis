from django.contrib import admin
from .models import Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "body", "created")
    search_fields = ("subject", "body")
    ordering = ("-created",)
