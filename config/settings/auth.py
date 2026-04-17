"""Authentication and user model settings."""

# Custom user model
AUTH_USER_MODEL = "users.User"

# Enable RFID authentication backend and restrict default admin login to localhost
# Keep LocalhostAdminBackend first so the localhost/IP checks run before password
# or OTP authentication. AccessPointLocalUserBackend follows immediately after
# because its localhost/local-network gate must run before credential-based
# backends (PasswordOrOTPBackend, TempPasswordBackend, and RFIDBackend).
AUTHENTICATION_BACKENDS = [
    "apps.users.backends.LocalhostAdminBackend",
    "apps.users.backends.AccessPointLocalUserBackend",
    "apps.users.backends.PasswordOrOTPBackend",
    "apps.users.backends.TempPasswordBackend",
    "apps.users.backends.RFIDBackend",
]

# Use the custom login view for all authentication redirects.
LOGIN_URL = "pages:login"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]
