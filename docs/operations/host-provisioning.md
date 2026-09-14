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

The managed ownership contract distinguishes disposable/replaceable resources such as the managed checkout, Python environment, logs, cache, and runtime files from persistent instance data under `/opt/arthexis/var/lib`. Re-running install does not replace an existing managed database with a checkout-local database. Later managed uninstall work must preserve persistent instance data by default unless the operator explicitly requests destructive cleanup.

A developer checkout is never converted in place into the managed production checkout. GWAY constructs and owns the canonical managed checkout independently of any nearby developer source tree. The planned adoption path in issue #208 will construct the normal managed layout and transfer the instance state that should survive promotion while leaving the developer checkout intact.

## Managed lifecycle status

`gway arthexis status` reports lifecycle state rather than only the old compact application-health result. The report includes managed/unmanaged ownership, lifecycle state, installation identity, root/checkout/environment paths, persistent-data location, Arthexis version and Git revision, dirty managed-checkout state, node role, database location, pending migrations, expected GWAY service units, and application health.

Use `gway arthexis status --json` when another tool needs the same information in a stable structured form. A valid managed installation reports `healthy` only when the managed Python environment exists, migrations are current, the services expected for the node role are active, and application health is `GOOD`. Missing ownership metadata or a conflicting marker is reported as invalid; missing runtime resources, pending migrations, inactive services, or failed application health produce a degraded managed state with actionable problem entries.

An unmanaged developer checkout remains a valid `unmanaged` lifecycle state rather than being treated as a broken managed installation. The repository `status.sh` continues to belong to the local/developer lifecycle and is not replaced by GWAY.

## Generic lifecycle boundary

GWAY is the intended OS-agnostic production lifecycle boundary for Arthexis. The command surface is being completed in issue #208; current GWAY conventions provide managed install/upgrade and project operations around the `arthexis` project.

The repository lifecycle scripts remain the supported local/developer workflow rather than compatibility wrappers around GWAY. See the [Install & Lifecycle Scripts Manual](../development/install-lifecycle-scripts-manual.md) for that workflow.

Hardware detection may still be used by an Arthexis feature when a genuine device-specific operation requires it. It must not determine the node-management or provisioning model.
