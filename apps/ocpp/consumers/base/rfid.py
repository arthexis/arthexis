"""RFID/account lookup helpers for OCPP consumer transaction auth flows."""

import logging
from dataclasses import dataclass

from channels.db import database_sync_to_async
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.cards.models import RFID as CoreRFID
from apps.cards.models import RFIDAttempt
from apps.energy.models import CustomerAccount
from apps.features.utils import get_cached_feature_enabled, get_cached_feature_parameter

from ... import store
from ...models import Transaction

logger = logging.getLogger(__name__)

ENERGY_ACCOUNTS_FEATURE_SLUG = "energy-accounts"
RFID_FALLBACK_ACCOUNT_FEATURE_SLUG = "rfid-fallback-account"
RFID_FALLBACK_ACCOUNT_NAME = _("RFID FALLBACK ACCOUNT")


@dataclass(frozen=True)
class AuthorizationDecision:
    """Normalized authorization decision payload for OCPP handlers."""

    status: str
    reason: str
    policy: str
    should_mark_seen: bool
    should_auto_enroll: bool
    log_unlinked_rfid: bool = False


class RfidMixin:
    """Provide RFID/account helper operations reused across transaction handlers."""

    async def _get_account(self, id_tag: str) -> CustomerAccount | None:
        """Return the customer account for the provided RFID if valid."""
        if not id_tag:
            return None

        def _resolve() -> CustomerAccount | None:
            matches = CoreRFID.matching_queryset(id_tag).filter(allowed=True)
            return CustomerAccount.objects.filter(rfids__in=matches).distinct().first()

        return await database_sync_to_async(_resolve)()

    async def _energy_accounts_enabled(self) -> bool:
        """Return whether account-first energy authorization is enabled."""

        return await database_sync_to_async(get_cached_feature_enabled)(
            ENERGY_ACCOUNTS_FEATURE_SLUG,
            cache_key="feature-enabled:energy-accounts",
            timeout=300,
            default=False,
        )


    async def _rfid_fallback_enabled(self) -> bool:
        """Return whether unknown RFIDs should auto-bind to a fallback debt account."""

        return await database_sync_to_async(get_cached_feature_enabled)(
            RFID_FALLBACK_ACCOUNT_FEATURE_SLUG,
            cache_key="feature-enabled:rfid-fallback-account",
            timeout=300,
            default=True,
        )

    def _resolve_fallback_account(self) -> CustomerAccount:
        """Return the default fallback account, ensuring it is a service account."""
        account, _created = CustomerAccount.objects.get_or_create(
            name=str(RFID_FALLBACK_ACCOUNT_NAME),
            defaults={"service_account": True},
        )
        if not account.service_account:
            account.service_account = True
            account.save(update_fields=["service_account"])
        return account

    async def _get_or_create_fallback_account(self) -> CustomerAccount:
        """Return the default fallback account used for unknown RFID debt tracking."""
        return await database_sync_to_async(self._resolve_fallback_account)()

    async def _bind_rfid_to_fallback_account(self, tag: CoreRFID) -> CustomerAccount:
        """Attach ``tag`` to the fallback account and return the account."""

        def _bind() -> CustomerAccount:
            account = self._resolve_fallback_account()
            account.rfids.add(tag)
            return account

        return await database_sync_to_async(_bind)()

    async def _bind_fallback_account_for_decision(
        self,
        decision: AuthorizationDecision,
        *,
        tag: CoreRFID | None,
        account: CustomerAccount | None,
    ) -> CustomerAccount | None:
        """Bind and return the fallback account when a fallback decision authorized the tag."""
        if decision.reason == "rfid_fallback_account_authorized" and tag:
            return await self._bind_rfid_to_fallback_account(tag)
        return account

    async def _apply_rfid_authorization_side_effects(
        self,
        *,
        id_tag: str,
        decision: AuthorizationDecision,
        tag: CoreRFID | None,
        tag_created: bool,
        account: CustomerAccount | None,
    ) -> tuple[CoreRFID | None, CustomerAccount | None]:
        """Apply tag visibility and fallback binding required by a decision."""
        if id_tag and decision.should_mark_seen:
            seen_tag = await self._ensure_rfid_seen(
                id_tag,
                tag=tag,
                tag_created=tag_created,
                auto_enroll=decision.should_auto_enroll,
            )
            if seen_tag:
                tag = seen_tag
        account = await self._bind_fallback_account_for_decision(
            decision,
            tag=tag,
            account=account,
        )
        return tag, account

    async def _energy_credits_required(self) -> bool:
        """Return whether positive credits are required for account authorization."""

        value = await database_sync_to_async(get_cached_feature_parameter)(
            ENERGY_ACCOUNTS_FEATURE_SLUG,
            "energy_credits_required",
            cache_key="feature-parameter:energy-accounts:energy_credits_required",
            timeout=300,
            fallback="disabled",
        )
        return value == "enabled"

    async def _evaluate_authorization_policy(
        self,
        *,
        id_tag: str,
        account: CustomerAccount | None,
        tag: CoreRFID | None,
        tag_created: bool,
    ) -> AuthorizationDecision:
        """Return one reasoned authorization decision for Authorize/Start/TransactionEvent."""

        policy = self.charger.resolved_authorization_policy()
        fallback_enabled = await self._rfid_fallback_enabled()

        energy_accounts_enabled = await self._energy_accounts_enabled()
        credits_required = await self._energy_credits_required()

        if policy == self.charger.AuthorizationPolicy.OPEN:
            return AuthorizationDecision(
                status="Accepted",
                reason="open_policy_insecure_compatibility_mode",
                policy=policy,
                should_mark_seen=bool(id_tag),
                should_auto_enroll=bool(id_tag),
            )

        if account is not None:
            if energy_accounts_enabled and not credits_required:
                return AuthorizationDecision(
                    status="Accepted",
                    reason="account_authorized_credits_not_required",
                    policy=policy,
                    should_mark_seen=bool(id_tag),
                    should_auto_enroll=False,
                )
            account_authorized = await database_sync_to_async(account.can_authorize)()
            if account_authorized:
                return AuthorizationDecision(
                    status="Accepted",
                    reason="account_authorized",
                    policy=policy,
                    should_mark_seen=bool(id_tag),
                    should_auto_enroll=False,
                )
            return AuthorizationDecision(
                status="Invalid",
                reason="account_not_authorized",
                policy=policy,
                should_mark_seen=bool(id_tag),
                should_auto_enroll=False,
            )

        if energy_accounts_enabled:
            return AuthorizationDecision(
                status="Invalid",
                reason="account_required_by_feature",
                policy=policy,
                should_mark_seen=bool(id_tag),
                should_auto_enroll=False,
            )

        if policy == self.charger.AuthorizationPolicy.ALLOWLIST:
            if id_tag and tag and not tag_created and tag.allowed:
                return AuthorizationDecision(
                    status="Accepted",
                    reason="allowlist_tag_authorized",
                    policy=policy,
                    should_mark_seen=True,
                    should_auto_enroll=False,
                    log_unlinked_rfid=True,
                )
            return AuthorizationDecision(
                status="Invalid",
                reason="allowlist_tag_not_authorized",
                policy=policy,
                should_mark_seen=bool(id_tag),
                should_auto_enroll=False,
            )

        if (
            fallback_enabled
            and policy == self.charger.AuthorizationPolicy.STRICT
            and id_tag
            and (tag is None or tag.allowed)
        ):
            return AuthorizationDecision(
                status="Accepted",
                reason="rfid_fallback_account_authorized",
                policy=policy,
                should_mark_seen=True,
                should_auto_enroll=True,
                log_unlinked_rfid=True,
            )

        return AuthorizationDecision(
            status="Invalid",
            reason="strict_account_required",
            policy=policy,
            should_mark_seen=bool(id_tag),
            should_auto_enroll=False,
        )

    async def _ensure_rfid_seen(
        self,
        id_tag: str,
        *,
        tag: CoreRFID | None = None,
        tag_created: bool = False,
        auto_enroll: bool = False,
    ) -> CoreRFID | None:
        """Ensure an RFID exists, update `last_seen_on`, and optionally auto-enroll it."""
        if not id_tag:
            return None

        normalized = id_tag.upper()

        def _ensure() -> CoreRFID:
            now = timezone.now()
            current_tag = tag
            current_tag_created = tag_created
            if current_tag is None:
                current_tag, current_tag_created = CoreRFID.register_scan(normalized)
            updates = ["last_seen_on"]
            current_tag.last_seen_on = now
            if auto_enroll and not current_tag.allowed:
                current_tag.allowed = True
                updates.append("allowed")
            if auto_enroll and not current_tag.released:
                current_tag.released = True
                updates.append("released")
            if auto_enroll and current_tag_created and not current_tag.discovered_via_ocpp:
                current_tag.discovered_via_ocpp = True
                updates.append("discovered_via_ocpp")
            current_tag.save(update_fields=sorted(set(updates)))
            return current_tag

        return await database_sync_to_async(_ensure)()

    def _log_unlinked_rfid(self, rfid: str, *, reason: str, policy: str) -> None:
        """Record a warning when an RFID is authorized without an account."""
        masked_rfid = rfid[-4:].rjust(len(rfid), "*") if len(rfid) > 4 else "****"
        message = (
            f"Authorized RFID {masked_rfid} on charger {self.charger_id} without linked customer account "
            f"(policy={policy}, reason={reason})"
        )
        logger.warning(message)
        store.add_log(store.pending_key(self.charger_id), message, log_type="charger")

    async def _record_rfid_attempt(
        self,
        *,
        rfid: str,
        status: RFIDAttempt.Status,
        account: CustomerAccount | None,
        transaction: Transaction | None = None,
        policy: str = "",
        reason: str = "",
    ) -> None:
        """Persist RFID session attempt metadata for reporting."""
        normalized = (rfid or "").strip().upper()
        if not normalized:
            return

        charger = self.charger

        def _create_attempt() -> None:
            RFIDAttempt.record_attempt(
                payload={
                    "authorization_policy": policy,
                    "authorization_reason": reason,
                    "rfid": normalized,
                },
                source=RFIDAttempt.Source.OCPP,
                status=status,
                charger_id=charger.pk,
                account_id=account.pk if account else None,
                transaction_id=transaction.pk if transaction else None,
            )

        await database_sync_to_async(_create_attempt)()
