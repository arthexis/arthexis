"""Custom auth migrations overriding Django's defaults."""

import os
import django.contrib.auth.migrations as auth_migrations

# Include the original Django auth migrations in the search path so we only
# need to provide overrides for migrations we customise.
__path__ = [os.path.dirname(__file__), os.path.dirname(auth_migrations.__file__)]
