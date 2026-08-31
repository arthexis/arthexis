"""Assembled Django settings package."""

from .base import *  # noqa: F401,F403
from .security import *  # noqa: F401,F403
from .apps import *  # noqa: F401,F403
from .middleware import *  # noqa: F401,F403
from .database import *  # noqa: F401,F403
from .i18n import *  # noqa: F401,F403
from .static import *  # noqa: F401,F403
from .logging import *  # noqa: F401,F403
from .celery import *  # noqa: F401,F403

from config.roles import validate_role_settings

validate_role_settings(globals())
