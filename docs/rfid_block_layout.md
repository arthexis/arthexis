# RFID Block Layout

Technicians configuring RFID cards must program block 8 with up to 16 bytes of ASCII data.

* **Keys**: Both Key A and Key B default to `FFFFFFFFFFFF`. Either key may be used for authentication.
* **Authentication**: The reader will attempt Key A first, then Key B, using `MFRC522_Auth` for block 8.
* **Data**: After authentication the reader fetches block 8 and stores the raw 16 byte payload. Printable
  ASCII data is returned in API responses as-is; otherwise the hexadecimal representation is used.

Ensure that other sectors remain at their default values so that authentication succeeds.
