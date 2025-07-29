# Accounts App

Users may authenticate using any RFID tag assigned to their account. POST the RFID value as JSON to `/accounts/rfid-login/` and the server will return the user's details if the tag matches one stored in the `RFID` model.

The `RFID` model stores card identifiers (8 hexadecimal digits). A tag may belong to a user and is `allowed` by default. Set `allowed` to `false` to disable it.

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

## RFID CSV Utilities

RFID tags can be exported and imported using management commands:

- `python manage.py export_rfids [path]` writes all tags to CSV. If `path` is omitted the data is printed to stdout.
- `python manage.py import_rfids path` loads tags from a CSV file created by the export command.
