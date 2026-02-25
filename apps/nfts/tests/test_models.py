"""Tests for NFT/RFID identity transfer behavior."""

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cards.models import RFID
from apps.nfts.models import NFT, RFIDBoundIdentity


class RFIDBoundIdentityTests(TestCase):
    """Verify NFT identity state transitions and validation rules."""

    def test_transfer_updates_current_rfid_and_creates_history(self) -> None:
        """Transferring an identity should update the active card and audit row."""

        nft = NFT.objects.create(token_id="token-1", payload=b"nft-bytes")
        source = RFID.objects.create(rfid="A1B2C3D4")
        target = RFID.objects.create(rfid="EEFF0011")
        identity = RFIDBoundIdentity.objects.create(
            identity_key="identity-1",
            nft=nft,
            current_rfid=source,
            payload_written_to_card=True,
        )

        transfer = identity.transfer_to_rfid(target, actor="qa")

        identity.refresh_from_db()
        self.assertEqual(identity.current_rfid, target)
        self.assertFalse(identity.payload_written_to_card)
        self.assertEqual(identity.transfers.count(), 1)
        self.assertEqual(transfer.from_rfid, source)
        self.assertEqual(transfer.to_rfid, target)


    def test_transfer_to_rfid_rejects_blocked_target(self) -> None:
        """Transferring to a blocked card should raise a validation error."""

        nft = NFT.objects.create(token_id="token-blocked")
        source = RFID.objects.create(rfid="SOURCE01")
        blocked = RFID.objects.create(rfid="BLOCKED01", allowed=False)
        identity = RFIDBoundIdentity.objects.create(
            identity_key="identity-blocked", nft=nft, current_rfid=source
        )

        with self.assertRaises(ValidationError):
            identity.transfer_to_rfid(blocked)

    def test_clean_rejects_blocked_target_card(self) -> None:
        """Binding to blocked cards should raise a validation error."""

        nft = NFT.objects.create(token_id="token-2")
        blocked = RFID.objects.create(rfid="1234ABCD", allowed=False)
        identity = RFIDBoundIdentity(identity_key="identity-2", nft=nft, current_rfid=blocked)

        with self.assertRaises(ValidationError):
            identity.full_clean()

    def test_sync_payload_from_nft_copies_payload_bytes(self) -> None:
        """The card payload should mirror NFT bytes for on-card storage."""

        nft = NFT.objects.create(token_id="token-3", payload=b"abc123")
        identity = RFIDBoundIdentity.objects.create(identity_key="identity-3", nft=nft)

        identity.sync_payload_from_nft()
        identity.refresh_from_db()

        self.assertEqual(bytes(identity.card_payload), b"abc123")
        self.assertFalse(identity.payload_written_to_card)
