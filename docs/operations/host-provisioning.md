# Host Provisioning Boundary

Arthexis manages Arthexis nodes. Provisioning the host operating system, imaging physical media, and administering an operating-system fleet are outside the Arthexis suite's scope.

Install Arthexis onto an already provisioned supported operating system. Once installed, a node is managed by its Arthexis identity, role, features, configuration, services, and application state rather than by the hardware or OS provisioning method that created the host.

## Raspberry Pi deployments

For Raspberry Pi hosts:

1. Install a normal supported Raspberry Pi OS image with Raspberry Pi Imager or other official Raspberry Pi tooling.
2. Configure OS-level concerns such as the user account, storage, networking, and SSH with the OS/vendor tooling.
3. Boot the host and verify normal OS access.
4. Install `gway`.
5. Install/bootstrap Arthexis through the generic Arthexis lifecycle path.
6. Register/configure the Arthexis node and assign its role and features.
7. Manage it thereafter as an ordinary Arthexis node.

Arthexis does not build or burn Raspberry Pi OS images, copy host network credentials into images, manage Raspberry Pi Connect devices, publish image releases, or orchestrate OS rollout campaigns.

## Generic lifecycle boundary

`gway` is the intended OS-agnostic lifecycle boundary for Arthexis installation, status, updates, and removal. The target command surface tracked by issue #201 is approximately:

```text
gway arthexis install
gway arthexis status
gway arthexis update
gway arthexis uninstall
```

That generic lifecycle work is being implemented separately from removal of the retired imaging/fleet architecture. Until those commands are available, follow the repository's current install and lifecycle scripts documented in the [Install & Lifecycle Scripts Manual](../development/install-lifecycle-scripts-manual.md); do not use retired imaging or Raspberry Pi Connect workflows as a provisioning substitute.

Hardware detection may still be used by an Arthexis feature when a genuine device-specific operation requires it. It must not determine the node-management or provisioning model.
