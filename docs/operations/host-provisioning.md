# Host Provisioning Boundary

Arthexis manages Arthexis nodes. Provisioning the host operating system, imaging physical media, and administering an operating-system fleet are outside the Arthexis suite's scope.

Install Arthexis onto an already provisioned supported operating system. Once installed, a node is managed by its Arthexis identity, role, features, configuration, services, and application state rather than by the hardware or OS provisioning method that created the host.

## Raspberry Pi deployments

For Raspberry Pi hosts:

1. Install a normal supported Raspberry Pi OS image with Raspberry Pi Imager or other official Raspberry Pi tooling.
2. Configure OS-level concerns such as the user account, storage, networking, and SSH with the OS/vendor tooling.
3. Boot the host and verify normal OS access.
4. Install `gway` for a managed production deployment, or use the repository lifecycle scripts for local development.
5. Install/bootstrap Arthexis through the selected application lifecycle.
6. Register/configure the Arthexis node and assign its role and features.
7. Manage it thereafter as an ordinary Arthexis node.

Arthexis does not build or burn Raspberry Pi OS images, copy host network credentials into images, manage Raspberry Pi Connect devices, publish image releases, or orchestrate OS rollout campaigns.

## Application lifecycle modes

Arthexis supports two lifecycle ownership modes. They are intentionally separate even when both run the same application code.

### Developer / unmanaged

A developer checkout remains unmanaged by GWAY. The repository lifecycle scripts such as `install.sh`, `upgrade.sh`, `status.sh`, and `uninstall.sh` remain supported developer/local entry points. A source checkout may use branches, dirty files, editable dependencies, checkout-local state, or other development conventions without becoming a production-managed installation.

The presence of an Arthexis checkout, virtual environment, database, lock files, or running processes does not by itself grant GWAY ownership of that checkout.

### GWAY-managed / production

A managed production installation uses the layout declared by `gway.toml`, currently rooted at `/opt/arthexis` with the managed checkout at `/opt/arthexis/app` and Python environment at `/opt/arthexis/.venv`.

For a complete production bootstrap, install the manifest-defined service topology as part of the GWAY install operation. The service profile must match the Arthexis node role. A new installation defaults to the `Terminal` role, so its complete bootstrap is:

```bash
export GWAY_SERVICE_PROFILE=Terminal
sudo --preserve-env=GWAY_SERVICE_PROFILE gway install arthexis --service
```

For another role, select the same role for both the Arthexis lifecycle arguments and the GWAY service profile. GWAY passes install options it does not own through to the project's lifecycle hook. For example, a Control node can be bootstrapped with:

```bash
export GWAY_SERVICE_PROFILE=Control
sudo --preserve-env=GWAY_SERVICE_PROFILE gway install arthexis --service --role Control
```

The `--service` option makes GWAY install, enable, and start the services selected by the profile after application preparation. Repeating the same install is supported: GWAY reuses the canonical managed checkout/environment, Arthexis reruns idempotent application preparation, existing persistent data is retained, the managed installation identity is preserved, and the selected service topology is reconciled/restarted.

Host prerequisites remain an operating-system concern. Arthexis may detect a missing prerequisite and report OS-appropriate installation guidance, but the application lifecycle does not silently turn itself into a host provisioner. In particular, roles using local Celery/Channels infrastructure report when their configured local Redis endpoint is unavailable.

After successful application preparation, Arthexis records managed ownership metadata under the managed root. Lifecycle inspection requires that metadata to agree with the expected managed layout before treating the installation as managed. Missing, malformed, or conflicting metadata is not silently repaired or interpreted as permission to operate on an arbitrary checkout.

The managed ownership contract distinguishes disposable/replaceable resources such as the managed checkout, Python environment, logs, cache, and runtime files from persistent instance data under `/opt/arthexis/var/lib`. Re-running install does not replace an existing managed database with a checkout-local database. A normal managed uninstall preserves `/opt/arthexis/var/lib` by default.

A developer checkout is never converted in place into the managed production checkout. GWAY constructs and owns the canonical managed checkout independently of any nearby developer source tree. The planned adoption path in issue #208 will construct the normal managed layout and transfer the instance state that should survive promotion while leaving the developer checkout intact.

## Managed lifecycle status

`gway arthexis status` reports lifecycle state rather than only the old compact application-health result. The report includes managed/unmanaged ownership, lifecycle state, installation identity, root/checkout/environment paths, persistent-data location, Arthexis version and Git revision, dirty managed-checkout state, node role, database location, pending migrations, expected GWAY service units, and application health.

Use `gway arthexis status --json` when another tool needs the same information in a stable structured form. A valid managed installation reports `healthy` only when the managed Python environment exists, migrations are current, the services expected for the node role are active, and application health is `GOOD`. Missing ownership metadata or a conflicting marker is reported as invalid; missing runtime resources, pending migrations, inactive services, or failed application health produce a degraded managed state with actionable problem entries.

An unmanaged developer checkout remains a valid `unmanaged` lifecycle state rather than being treated as a broken managed installation. The repository `status.sh` continues to belong to the local/developer lifecycle and is not replaced by GWAY.

## Managed updates

`gway upgrade arthexis` is the production update entry point. GWAY owns the managed repository and Python-environment refresh, including its clean-checkout safety checks and rollback of those resources when the application lifecycle hook fails. Arthexis then completes the application-specific transaction in this order:

1. require valid managed ownership metadata before mutating application state;
2. preserve the existing node role and persistent data while running migrations, local-node reconciliation, and static-asset collection;
3. reconcile the GWAY-managed service topology using the persisted Arthexis role as `GWAY_SERVICE_PROFILE`;
4. run lifecycle status after the service restart and require the managed installation to remain healthy;
5. confirm the managed ownership marker only after all preceding phases succeed.

The update hook reports the failing phase (`application preparation`, `service reconciliation`, `health verification`, or `ownership confirmation`) so the outer GWAY command retains useful failure context. The managed database and other persistent instance state under `/opt/arthexis/var/lib` are not replaced by checkout contents during an update, and the installation identity remains stable.

A normal managed upgrade does not silently discard local changes in `/opt/arthexis/app`. GWAY refuses a dirty managed checkout by default. Operators may deliberately use GWAY's `--force` or `--try-force` policies when they have decided how those managed-checkout modifications should be handled; forced-update history remains a GWAY concern rather than an Arthexis-specific implementation.

When the tracked revision is already current and the managed checkout is clean, GWAY may skip the update. `gway upgrade arthexis --reload` deliberately reruns environment refresh and the managed application update transaction even when the revision has not changed, which is useful for recovery or verification.

This production update path does not replace `upgrade.sh`. The repository script remains the supported upgrade mechanism for unmanaged developer/local checkouts and may continue to support developer-oriented branch, stash, target, and local-change workflows that do not belong in managed production ownership.

## Managed uninstall

`sudo gway uninstall arthexis` retires a GWAY-managed production installation according to the same ownership boundary used by install, status, and update.

GWAY first stops and removes the manifest-defined system services while the managed checkout still exists. It then runs the Arthexis uninstall lifecycle hook from the managed Python environment. The hook requires valid managed ownership metadata, removes application-owned disposable state such as logs, cache, runtime files, and the ownership marker, and deliberately leaves `/opt/arthexis/var/lib` untouched. Only after that hook succeeds does GWAY remove the managed Python environment and checkout and unregister the project.

The default uninstall therefore removes the production runtime but preserves instance data. There is intentionally no implicit purge mode in this lifecycle stage: passing a destructive data-removal argument to the Arthexis uninstall hook is rejected rather than interpreted as permission to delete the database or other persistent state. Persistent-data deletion, if ever added, must be a separate explicit destructive contract.

If the Arthexis uninstall hook fails, GWAY leaves the checkout, environment, and registry entry in place so the failure can be diagnosed and the operation retried. A later uninstall request after successful removal is expected to report that the project is no longer registered rather than touching the preserved data directory.

This production uninstall path does not replace `uninstall.sh`; the repository script remains part of the developer/local ownership model.

## Generic lifecycle boundary

GWAY is the intended OS-agnostic production lifecycle boundary for Arthexis. The command surface is being completed in issue #208; current GWAY conventions provide managed install/upgrade/uninstall and project operations around the `arthexis` project.

The repository lifecycle scripts remain the supported local/developer workflow rather than compatibility wrappers around GWAY. See the [Install & Lifecycle Scripts Manual](../development/install-lifecycle-scripts-manual.md) for that workflow.

Hardware detection may still be used by an Arthexis feature when a genuine device-specific operation requires it. It must not determine the node-management or provisioning model.
