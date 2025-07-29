# Accounts App

Users may authenticate using any RFID tag assigned to their account. POST the UID as JSON to `/accounts/rfid-login/` and the server will return the user's details if the tag matches one stored in the `RFID` model.

The `RFID` model stores card identifiers. A tag may belong to a user or be marked as `blacklisted` to disable it.

## Account Credits

Each user may have an associated **Account** record that tracks available energy credits. The model stores:

- `credits_kwh` – total kWh purchased or granted to the user.
- `total_kwh_spent` – kWh consumed so far.
- `balance_kwh` – property returning the remaining credit.

The account is linked to the user with a one‑to‑one relationship and can be referenced during authorization or billing steps.
