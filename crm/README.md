# CRM App

Provides customer relationship management features, including an integration with an Odoo server.
The `crm.odoo` sub-app stores connection credentials in the `Instance` model and exposes a
`/odoo/test/<id>/` endpoint to verify authentication. Instances can be managed through the
Django admin under **Relationship Managers**, where a **Test connection** action attempts to
authenticate with the selected servers.
