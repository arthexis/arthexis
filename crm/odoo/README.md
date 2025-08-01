# Odoo App

Provides a simple integration with an Odoo server. The `Instance` model stores
connection credentials and a `/odoo/test/<id>/` endpoint checks whether the
specified instance can be authenticated. Instances can be managed through the
Django admin where a **Test connection** action attempts to authenticate with
the selected servers.
