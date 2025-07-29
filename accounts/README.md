# Accounts App

Users may authenticate using the UID of an RFID card. POST the UID as JSON to `/accounts/rfid-login/` and the server will return the user's details if the UID matches an existing account.

## Account Credits

Each user may have an associated **Account** record that tracks available energy credits. The model stores:

- `credits_kwh` – total kWh purchased or granted to the user.
- `total_kwh_spent` – kWh consumed so far.
- `balance_kwh` – property returning the remaining credit.

The account is linked to the user with a one‑to‑one relationship and can be referenced during authorization or billing steps.

## Vehicles

An account may be associated with multiple **Vehicle** records. Each vehicle
stores the `brand`, `model` and `vin` (Vehicle Identification Number) so that a
user's cars can be identified when using OCPP chargers.
