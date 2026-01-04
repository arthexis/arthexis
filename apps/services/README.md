# Services app

The `apps.services` app owns operational checks that were previously embedded in the nodes domain. It manages `NodeService` definitions alongside their systemd unit templates so validations can run in isolation from node-specific admin screens.

## Settings
- `SERVICES_TEMPLATE_DIR`: Optional override for where service templates are loaded from. When unset, templates bundled under `apps/services/service_templates/` are used.
- `SERVICES_SYSTEMD_DIR`: Optional override for the systemd unit directory. It defaults to `SYSTEMD_DIR` when present or `/etc/systemd/system`.

## Operational checks
Service health checks live in this app. Admin actions validate rendered templates and active unit states without requiring the nodes admin area. The `NodeService.compare_to_installed` helper can be invoked from management commands or Celery tasks by providing the base path for template context and the target systemd directory when running remotely.
