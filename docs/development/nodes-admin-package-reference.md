# Nodes Admin Package Reference

Reference map for `apps.nodes.admin`.

- `forms.py` — admin-only forms such as `NodeAdminForm`, firmware/DataTransfer helpers, and NetMessage forms. Admin classes import shared forms from here instead of defining them inline.
- `inlines.py` — inline admin configurations, including `NodeFeatureAssignmentInline`.
- `node_admin.py` — primary admin for `Node`, including visitor registration, firmware/DataTransfer/OCPP helpers, and diagnostics/update actions.
- `email_outbox_admin.py` — email outbox admin tooling and test endpoint.
- `node_role_admin.py` — admin for `NodeRole` with role-to-node assignment form.
- `platform_admin.py` — admin for `Platform` hardware/OS metadata.
- `node_feature_admin.py` — admin for `NodeFeature` plus feature eligibility checks and device diagnostics (audio, screenshots, camera stream).
- `net_message_admin.py` — admin for `NetMessage`, including quick-send tooling and resend endpoints.
