# Nodes admin package

[![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/)

The Django admin setup for the nodes app is split across focused modules to keep the large surface area manageable:

- `forms.py` — admin-only forms such as `NodeAdminForm`, firmware/DataTransfer helpers, and NetMessage forms. All admin classes import shared forms from here rather than defining them inline.
- `inlines.py` — inline admin configurations (currently `NodeFeatureAssignmentInline`).
- `node_admin.py` — primary admin for `Node` including visitor registration, firmware/DataTransfer/OCPP helpers, and diagnostics/update actions.
- `email_outbox_admin.py` — email outbox admin tooling and test endpoint.
- `node_role_admin.py` — admin for `NodeRole` with role-to-node assignment form.
- `platform_admin.py` — admin for `Platform` hardware/OS metadata.
- `node_feature_admin.py` — admin for `NodeFeature` plus feature eligibility checks and device diagnostics (audio, screenshots, camera stream).
- `net_message_admin.py` — admin for `NetMessage` including quick-send tooling and resend endpoints.

Import `apps.nodes.admin` (the package) to ensure all admin registrations are evaluated; `__init__.py` re-exports the registered admin classes for convenience.
