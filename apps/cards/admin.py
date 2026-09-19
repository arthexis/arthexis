from django.contrib import admin

from apps.cards.models import AuthorizationAttempt, CardCredential


@admin.register(CardCredential)
class CardCredentialAdmin(admin.ModelAdmin):
    list_display = ("external_id", "label", "active", "account")
    list_filter = ("active",)
    search_fields = ("external_id", "label", "ocpp_id_tag")


@admin.register(AuthorizationAttempt)
class AuthorizationAttemptAdmin(admin.ModelAdmin):
    list_display = ("presented_id", "accepted", "occurred_at")
    list_filter = ("accepted",)
    search_fields = ("presented_id", "reason")
