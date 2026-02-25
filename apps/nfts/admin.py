"""Admin registration for NFT models."""

from django.contrib import admin

from apps.nfts.models import NFT, NFTTransfer, RFIDBoundIdentity


@admin.register(NFT)
class NFTAdmin(admin.ModelAdmin):
    """Admin for NFT inventory objects."""

    list_display = ("token_id", "name", "payload_mime_type")
    search_fields = ("token_id", "name")


@admin.register(RFIDBoundIdentity)
class RFIDBoundIdentityAdmin(admin.ModelAdmin):
    """Admin for logical identities that move across physical RFID cards."""

    list_display = (
        "identity_key",
        "nft",
        "current_rfid",
        "payload_written_to_card",
        "last_transferred_on",
    )
    list_filter = ("payload_written_to_card",)
    search_fields = ("identity_key", "nft__token_id", "current_rfid__rfid")
    autocomplete_fields = ("nft", "current_rfid")


@admin.register(NFTTransfer)
class NFTTransferAdmin(admin.ModelAdmin):
    """Admin for NFT identity transfer audit history."""

    list_display = ("identity", "from_rfid", "to_rfid", "actor", "transferred_on")
    search_fields = (
        "identity__identity_key",
        "from_rfid__rfid",
        "to_rfid__rfid",
        "actor",
    )
    autocomplete_fields = ("identity", "from_rfid", "to_rfid")
