# Node Features Cookbook

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
3. The changelist is registered in [`apps/nodes/admin.py`](../../apps/nodes/admin.py#L1955-L2144) and displays columns for the display name, slug, default roles, enablement status, and available actions.

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
| AP Router | `ap-router` | Satellite | Auto-managed network access point capability for satellites. |
| Audio Capture | `audio-capture` | Control | Manual feature with a **Test Microphone** admin action for verification. |
| Celery Queue | `celery-queue` | Satellite, Control, Watchtower | Auto-managed feature with a **Celery Report** admin action. |
| GUI Toast | `gui-toast` | Terminal, Control | Auto-managed feature that surfaces GUI toast notifications when supported. |
| LCD Screen | `lcd-screen` | Control | Auto-managed flag for nodes driving an attached LCD panel. |
| NGINX Server | `nginx-server` | Satellite, Control, Watchtower | Auto-managed flag for nodes running the bundled NGINX front end. |
| RFID Scanner | `rfid-scanner` | Control, Satellite | Auto-managed feature with a **Scan RFIDs** admin action. |
| Raspberry Pi Camera | `rpi-camera` | Control, Satellite | Auto-managed feature with built-in eligibility checks, **Take a Snapshot**/**View stream** default actions, and RFID/QR snapshot and scan integrations. |
| Screenshot Poll | `screenshot-poll` | (manual enablement) | Manual feature providing a **Take Screenshot** admin action. |

Manual features (those without default roles) still appear in the changelist and can be enabled through the admin action once local hardware or environment checks pass.

## Assigning features to roles and nodes

Node roles and individual nodes both control feature availability:

- **Default roles** – Selecting roles on the feature form ensures new nodes that join the role automatically inherit the feature. The admin prepopulates `NodeFeature.roles` through the horizontal selector.
- **Node-specific assignments** – Open a node change form (`admin:nodes_node_change`) to adjust the inline **Node feature assignments** table. The inline is provided by `NodeFeatureAssignmentInline` (`apps/nodes/admin.py` lines 115-144) and writes to the `NodeFeatureAssignment` through model.

Changes to assignments are applied immediately after saving the node or feature form. Use Django’s history view to audit adjustments.

## Running eligibility checks

Select one or more features on the changelist and choose **Check features for eligibility**. The admin action (`NodeFeatureAdmin.check_features_for_eligibility`) calls the registry in [`apps/nodes/feature_checks.py`](../../apps/nodes/feature_checks.py) to evaluate whether the local node satisfies hardware or software requirements. Results appear as Django messages with success, warning, or error levels.

Eligibility runs also report whether a feature can be enabled manually. The helper `_manual_enablement_message` in `NodeFeatureAdmin` communicates whether the feature belongs to `Node.MANUAL_FEATURE_SLUGS` or requires automation.

## Declaring feature setup hooks

Auto-managed features discover their enablement and lifecycle through app-level hooks:

- Add a `node_features.py` module to an app.
- Export `check_node_feature(slug, *, node)` to return `True` when a feature could be meaningfully enabled on the provided node. Return `None` when the slug is unknown to the module.
- Export `setup_node_feature(slug, *, node)` for any setup or side-effects the feature needs during auto-detection (returning `True`/`False` mirrors `check_node_feature`).

The nodes app loads these hooks from every installed app so features can own their own detection logic instead of being hard-coded in `apps.nodes.models.Node`.

## Enabling features manually

To toggle features outside of automatic provisioning, select them in the changelist and execute **Enable selected features**. When a local node is registered, the action creates `NodeFeatureAssignment` rows for that node (see `NodeFeatureAssignment.objects.update_or_create` calls in [`apps/nodes/models.py`](../../apps/nodes/models.py#L1009-L1191)).

If no local node exists, the admin posts an informational message. Register a node first via the Nodes interface, then re-run the enable action.

## Troubleshooting

- **Actions menu shows em dash (—)** – The feature is not currently enabled, or no default actions exist. Enable the feature or configure `DEFAULT_ACTIONS` in [`apps/nodes/models.py`](../../apps/nodes/models.py).
- **Eligibility checks always warn about missing checks** – Add an implementation to the `feature_checks` registry in [`apps/nodes/feature_checks.py`](../../apps/nodes/feature_checks.py) to cover the slug in question.
- **Assignments disappear after deployments** – Verify that fixtures in `nodes/fixtures/` or migrations are not removing the feature (`nodes/migrations/0018`, `0027`, etc.). Reapply role assignments if a migration intentionally deprecates the feature.
