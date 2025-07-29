# QRCodes App

Provides a small `QRLink` model that stores a value and generates a QR image for it. A template tag `qr_img` renders the QR code in templates and automatically creates the record if needed.

A simple landing page at `/qr/` can generate a QR code for arbitrary text without saving anything to the database.
