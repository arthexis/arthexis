# Node features

Coordinate capabilities across nodes by managing `nodes.NodeFeature` records in the Django admin.

- [Accessing node features](#accessing-node-features)
- [Reviewing feature metadata](#reviewing-feature-metadata)
- [Current feature catalog](#current-feature-catalog)
- [Assigning features to roles and nodes](#assigning-features-to-roles-and-nodes)
- [Running eligibility checks](#running-eligibility-checks)
- [Enabling features manually](#enabling-features-manually)
- [Troubleshooting](#troubleshooting)

## Accessing node features

1. Open the Django admin and locate the **Nodes** application.
2. Click **Node features** (model `nodes.NodeFeature`) to open the changelist (`admin:nodes_nodefeature_changelist`).
3. The changelist is registered in [`apps/nodes/admin/node_feature_admin.py`](../../nodes/admin/node_feature_admin.py) and displays columns for the display name, slug, default roles, enablement status, and available actions.

## Reviewing feature metadata

Each feature encapsulates a capability that nodes can expose. The admin form provides:

- **Slug** – A unique identifier that code paths reference. Ensure slugs remain stable across releases.
- **Display** – A human-friendly label surfaced in the UI.
- **Description** – Operational context for administrators.
- **Roles** – Default node roles that should receive the feature (`filter_horizontal = ("roles",)` in `NodeFeatureAdmin`).
- **Default actions** – Optional links that appear in the `Actions` column when a feature is enabled (`NodeFeature.get_default_actions`).

Use the search field to find features by slug or display string. The queryset prefetches role relations, keeping list navigation fast even with many records.

## Current feature catalog

| Feature | Slug | Default roles | Key actions / notes |
| --- | --- | --- | --- |
| Celery Queue | `celery-queue` | Satellite, Control, Watchtower | Auto-managed feature with a **Celery Report** admin action. |
| GUI Toast | `gui-toast` | Terminal, Control | Auto-managed feature that surfaces GUI toast notifications when supported. |
| LCD Screen | `lcd-screen` | Control | Auto-managed flag for nodes driving an attached LCD panel. |
| NGINX Server | `nginx-server` | Satellite, Control, Watchtower | Auto-managed flag for nodes running the bundled NGINX front end. |
| RFID Scanner | `rfid-scanner` | Control, Satellite | Auto-managed feature with a **Scanner** admin action. |
| Playwright Automation | `playwright-automation` | (auto-detected) | Wrapper node capability that must be active before any Playwright browser engine feature can run. |
| Playwright Chromium | `playwright-browser-chromium` | (auto-detected) | Engine-specific Playwright node capability. |
| Playwright Firefox | `playwright-browser-firefox` | (auto-detected) | Engine-specific Playwright node capability. |
| Playwright WebKit | `playwright-browser-webkit` | (auto-detected) | Engine-specific Playwright node capability. |
| Video Camera | `video-cam` | (manual enablement) | Auto-managed feature with built-in eligibility checks, **Take a Snapshot**/**View stream** default actions, and RFID/QR snapshot and scan integrations. |
| Screenshot Poll | `screenshot-poll` | (manual enablement) | Manual feature providing a **Take Screenshot** admin action. |

Features without default roles still appear in the changelist and can be enabled through admin actions once local hardware or environment checks pass.

## Assigning features to roles and nodes

Node roles and individual nodes both control feature availability:

- **Default roles** – Selecting roles on the feature form ensures new nodes that join the role automatically inherit the feature. The admin prepopulates `NodeFeature.roles` through the horizontal selector.
- **Node-specific assignments** – Open a node change form (`admin:nodes_node_change`) to adjust the inline **Node feature assignments** table. The inline is provided by `NodeFeatureAssignmentInline` (`apps/nodes/admin/inlines.py`) and writes to the `NodeFeatureAssignment` through model.

Changes to assignments are applied immediately after saving the node or feature form. Use Django’s history view to audit adjustments.

## Running eligibility checks

Select one or more features on the changelist and choose **Check features for eligibility**. The admin action (`NodeFeatureAdmin.check_features_for_eligibility`) calls the registry in [`nodes/feature_checks.py`](../../nodes/feature_checks.py) to evaluate whether the local node satisfies hardware or software requirements. Results appear as Django messages (success, warning, or error).

Eligibility runs also report whether a feature can be enabled manually. The helper `_manual_enablement_data` in `NodeFeatureAdmin` communicates whether the feature belongs to `Node.MANUAL_FEATURE_SLUGS` or requires automation.

## Playwright global vs engine-level controls

Playwright execution now uses **two gates**:

1. **Suite feature gate**: `playwright-automation` (global toggle in Suite Features).
2. **Node feature gate**: engine-specific node feature (`playwright-browser-chromium`, `playwright-browser-firefox`, `playwright-browser-webkit`).

An engine can run only when both gates are open. Practical effects:

- If `playwright-automation` is disabled, browser launch checks and admin browser-test actions short-circuit globally.
- If `playwright-automation` is enabled but a specific `playwright-browser-*` node feature is disabled, that engine is rejected on the local node.

Use this pattern to separate policy from capability:

- **Policy** (all Playwright runtime execution): Suite Feature.
- **Capability** (which browser engine can run on a node): Node Features.

## Declaring feature setup hooks

Auto-managed features discover their enablement and lifecycle through app-level hooks:

- Add a `node_features.py` module to an app.
- Export `check_node_feature(slug, *, node, base_dir=None, base_path=None)` to return `True` when a feature could be meaningfully enabled on the provided node. Return `None` when the slug is unknown to the module.
- Export `setup_node_feature(slug, *, node, base_dir=None, base_path=None)` for any setup or side-effects the feature needs during auto-detection (returning `True`/`False` mirrors `check_node_feature`).
- Export `register_node_feature_detection(registry)` and register one detector per feature slug using `registry.register(slug, check=..., setup=...)`.

The nodes app discovers and orchestrates these detectors from every installed app so each feature can own its own auto-detection lifecycle.

## Enabling features manually

To toggle features outside of automatic provisioning, select them in the changelist and execute **Enable selected features**. When a local node is registered, the action creates `NodeFeatureAssignment` rows for that node (see `NodeFeatureAssignment.objects.update_or_create` calls in [`apps/nodes/models/features.py`](../../nodes/models/features.py)).

If no local node exists, the admin posts an informational message. Register a node first via the Nodes interface, then re-run the enable action.

## Troubleshooting

- **Actions menu shows em dash (—)** – The feature is not currently enabled, or no default actions exist. Enable the feature or configure `DEFAULT_ACTIONS` in [`apps/nodes/models/features.py`](../../nodes/models/features.py).
- **Eligibility checks always warn about missing checks** – Add an implementation to the `feature_checks` registry in [`nodes/feature_checks.py`](../../nodes/feature_checks.py) to cover the slug in question.
- **Assignments disappear after deployments** – Verify that fixtures in `nodes/fixtures/` or migrations are not removing the feature (`nodes/migrations/0018`, `0027`, etc.). Reapply role assignments if a migration intentionally deprecates the feature.
