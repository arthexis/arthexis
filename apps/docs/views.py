"""Compatibility shim for the retained public README fallback.

The runtime documentation application has been removed; README rendering now belongs
to the sites application.
"""

from apps.sites.readme import render_readme_page

__all__ = ["render_readme_page"]
