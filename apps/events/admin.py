from django.contrib import admin

from apps.events.models import EventEnvelope


@admin.register(EventEnvelope)
class EventEnvelopeAdmin(admin.ModelAdmin):
    list_display = ("event_type", "producer", "created_at", "published_at")
    list_filter = ("event_type", "producer")
    search_fields = ("event_type", "producer")
    readonly_fields = ("event_id", "created_at")
