# Proposal: use Energy Transactions as the unified energy ledger

## Context
- `CustomerAccount` tracks balances in currency and kW, and currently derives kW balance from `EnergyCredit` records plus consumed kW from charger transactions. Credits are tallied through `CustomerAccount.credits_kw`, while consumption is measured via `CustomerAccount.total_kw_spent` from `transactions` with meter deltas. [source: `apps/energy/models.py`]
- `EnergyCredit` is a lightweight kW top-up entry tied to an account and optional creator. It stores only the credited kW amount. [source: `apps/energy/models.py`]
- `EnergyTransaction` models kW purchases tied to a tariff, currency amount, and conversion factor, but it is not currently used for decrements. [source: `apps/energy/models.py`]

This setup splits credit additions (`EnergyCredit`) from purchase metadata (`EnergyTransaction`), making it harder to audit balance changes in one place.

## Proposed direction
Use `EnergyTransaction` as the single ledger for both credits and debits, deprecating the separate `EnergyCredit` table. Each ledger row would represent a balance delta with metadata about how it was produced.

### Data model changes
1. **Extend `EnergyTransaction`**
   - Add a `direction` enum (`credit`, `debit`) and a signed `delta_kw` field (positive for credits, negative for debits) to represent balance impact directly.
   - Keep `charged_amount_mxn`/`conversion_factor` optional for non-payment credits (manual adjustments, promos). For debits originating from charge sessions, allow a foreign key to the charge record so meter deltas are traceable.
   - Add `source`/`reference` fields (e.g., `card_topup`, `subscription`, `session_close`, `manual_adjustment`) to categorize entries and allow filtering.

2. **Deprecate `EnergyCredit`**
   - Migrate existing rows into `EnergyTransaction` with `direction=credit`, `delta_kw=amount_kw`, and `source=manual_adjustment` or `card_topup` as applicable.
   - Update admin inlines and serializers to read/write through the unified transaction model.

3. **Account balance derivation**
   - Replace `CustomerAccount.credits_kw` with a sum of `EnergyTransaction.delta_kw` where `direction=credit` (or simply the signed sum across all ledger rows) to compute `balance_kw`.
   - Keep `total_kw_spent` sourced from charging sessions for reporting, but ensure debits created from sessions write a matching `EnergyTransaction` entry so the ledger and meter totals reconcile.

### Process flows
- **Top-ups (card, subscription, manual)**: create a credit `EnergyTransaction` with currency/tariff details when available. Update `charged_amount_mxn` and `conversion_factor` to preserve pricing evidence.
- **Session settlement**: when a charge completes, record a debit `EnergyTransaction` with `delta_kw` equal to meter consumption and link to the session. This replaces implicit balance decrements and keeps the ledger symmetric.
- **Adjustments/refunds**: create reversing entries (credit for refund, debit for correction) instead of mutating balances, preserving auditability.

### Benefits
- Single source of truth for balance movements (easier statements and audits).
- Rich metadata on both credits and debits (tariff, price, source) without needing parallel tables.
- Simplified balance computation and reporting APIs that can paginate/filter a single ledger stream.

### Migration & rollout (no existing data)
Because we have no production data yet, the shortest path is to skip backfills and switch immediately:

1. Add `direction`/`delta_kw` to `EnergyTransaction` and start writing all balance movements there (credits and debits).
2. Update balance calculations and authorization checks to use the ledger sums; add indexes on `(account, created_on, direction)` for efficient queries.
3. Stop writing to `EnergyCredit` entirely, then remove the model, admin, and serializers once the new ledger paths are wired.

### Considerations
- Ensure idempotency of session-close jobs so debits are not duplicated.
- Decide on precision/rounding rules for `delta_kw` vs meter deltas to avoid drift.
- Provide reconciliation tooling that compares aggregated debits with session meter totals and flags discrepancies before removing `EnergyCredit`.
