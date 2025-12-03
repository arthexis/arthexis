from __future__ import annotations

from ..site_config import ensure_site_fields
from .landing import Landing, LandingManager
from .landing_lead import LandingLead
from .module import Module, ModuleManager
from .site_badge import SiteBadge
from .site_proxy import SiteProxy
from .site_template import SiteTemplate, SiteTemplateManager
from .user_story import UserStory
from .view_history import ViewHistory

ensure_site_fields()

# Import signal handlers.
from . import signals  # noqa: E402,F401


def _create_landings(sender, instance: Module, created: bool, raw: bool, **kwargs):
    """Compat shim for legacy imports expecting a post-save handler."""

    instance.handle_post_save(created=created, raw=raw)

__all__ = [
    "Landing",
    "LandingLead",
    "LandingManager",
    "Module",
    "ModuleManager",
    "SiteBadge",
    "SiteProxy",
    "SiteTemplate",
    "SiteTemplateManager",
    "UserStory",
    "ViewHistory",
    "_create_landings",
]
