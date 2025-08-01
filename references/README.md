# References App

Provides a small `Reference` model that stores values or links which can be represented by QR codes or other methods. Each
reference can store alternative text and tracks how often it is used. A template tag `ref_img` renders the QR image in templates
and automatically creates or updates the record when needed.

A simple landing page at `/ref/` can generate a QR code for arbitrary text without saving anything to the database.

