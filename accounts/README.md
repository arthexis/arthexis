# Accounts and Products App

Users may authenticate using any RFID tag assigned to their account. POST the RFID value as JSON to `/accounts/rfid-login/` and the server will return the user's details if the tag matches one stored in the `RFID` model.

The `RFID` model stores card identifiers (8 hexadecimal digits). A tag may belong to a user and is `allowed` by default. Set `allowed` to `false` to disable it.

The `User` model has a **Contact** section containing optional `phone_number` and
`address` fields. The `address` field is a foreign key to the `Address` model
which stores `street`, `number`, `municipality`, `state` and `postal_code`.
Only municipalities from the Mexican states of Coahuila and Nuevo León are
accepted. The user model also includes a `has_charger` flag indicating whether
the user has a charger at that location.

The `RFID` model stores card identifiers (8 hexadecimal digits). A tag may belong to a user or be marked as `blacklisted` to disable it.

## Account Credits

Each user may have an associated **Account** record that tracks available energy credits.
Credits are added (or removed) in the Django admin by creating **Credit** entries.
Each entry stores the amount, who created it and when it was added so every
movement is tracked individually. Consumption is calculated from recorded
transactions. The account exposes:

- `credits_kwh` – sum of all credit amounts.
- `total_kwh_spent` – kWh consumed across transactions.
- `balance_kwh` – remaining credit after subtracting usage.

The account is linked to the user with a one‑to‑one relationship and can be
referenced during authorization or billing steps. Accounts include a **Service
Account** flag which, when enabled, bypasses balance checks during
authorization. The admin lists the current authorization status so staff can
quickly verify whether an account would be accepted by a charger.

## Vehicles

An account may be associated with multiple **Vehicle** records. Each vehicle
stores the `brand`, `model` and `VIN` (Vehicle Identification Number) so that a
user's cars can be identified when using OCPP chargers.

## Products and Subscriptions

Provides a simple subscription model:

- `GET /accounts/products/` returns available products.
- `POST /accounts/subscribe/` with `account_id` and `product_id` creates a subscription.
- `GET /accounts/list/?account_id=<id>` lists subscriptions for an account.

## RFID CSV Utilities

RFID tags can be exported and imported using management commands:

- `python manage.py export_rfids [path]` writes all tags to CSV. If `path` is omitted the data is printed to stdout.
- `python manage.py import_rfids path` loads tags from a CSV file created by the export command.
- The Django admin also provides export and import actions powered by [`django-import-export`](https://django-import-export.readthedocs.io/).

## RFID Batch API

- `GET /accounts/rfids/` returns all RFID tags with their associated accounts and allowed flag.
- `POST /accounts/rfids/` accepts JSON in the form `{ "rfids": [{"rfid": "ABCD1234", "accounts": [1,2], "allowed": true}] }` to import or update tags in bulk.
