from __future__ import annotations

from django.contrib.sites.models import Site


class SiteProxy(Site):
    class Meta:
        proxy = True
        app_label = "pages"
        verbose_name = "Site"
        verbose_name_plural = "Sites"
        default_permissions = ()
        permissions = [
            ("add_siteproxy", "Can add site"),
            ("change_siteproxy", "Can change site"),
            ("delete_siteproxy", "Can delete site"),
            ("view_siteproxy", "Can view site"),
        ]
