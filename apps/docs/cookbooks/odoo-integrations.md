# Odoo Integrations Cookbook

This cookbook documents how Arthexis Constellation connects to Odoo so teams can manage identity and catalog data from a single source of truth.

## Prerequisites
- An Odoo instance with API access enabled and user credentials that can read the relevant models.
- Network egress from your Constellation deployment to the Odoo host.
- The Odoo base URL and an API key or password with access to the integrations below.

> **Tip:** Configure these settings in your environment variables or secrets manager to keep credentials out of code and deployment manifests.

## Employee credential sync (`res.users`)
Arthexis mirrors employee records from `res.users` to keep login privileges and contact information aligned with your ERP:
- Fetches usernames, names, and email addresses for account provisioning.
- Stores active flags to automatically disable access when a user is archived in Odoo.
- Applies role mappings so administrators can grant Django permissions that parallel Odoo groups.

**Configuration hints**
- Set the Odoo connection URL, database name, username, and API key in your environment.
- Schedule the sync as a periodic Celery task to keep credentials fresh without manual intervention.
- Enable logging to trace imported users and permission assignments during audits.

## Product catalog lookups (`product.product`)
Constellation queries the `product.product` model to present up-to-date item data inside charging and billing workflows:
- Retrieves SKU identifiers, names, and descriptions for operator-facing screens.
- Pulls price lists and tax profiles to align billing with ERP policies.
- Uses variant attributes to show compatible components for specific chargers or installations.

**Configuration hints**
- Provide the catalog price list or pricelist rule you want exposed to Constellation users.
- Cache responses to minimize round-trips to Odoo while keeping TTLs short enough for accurate pricing.
- Validate products against required attributes (such as unit of measure) before using them in charging sessions.

## Monitoring and troubleshooting
- Record integration successes and failures in your observability stack so you can track availability over time.
- Use replayable job queues for bulk imports to ensure that intermittent Odoo outages do not drop catalog or user updates.
- Alert on authentication failures to catch expired API keys before they block provisioning or billing flows.
