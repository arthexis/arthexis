# Upgrade Guide

This guide tracks manual steps that administrators must run after applying
upgrades. The lifecycle scripts no longer perform these actions automatically,
so review this document whenever you move between releases.

## Legacy `wlan1-device-refresh` service

Earlier releases installed a `wlan1-device-refresh.service` unit that pointed to
a helper script removed from the repository. The upgrade workflow no longer
modifies network interfaces directly, so systems that were originally provisioned
with that service need a one-time cleanup to keep refreshing the `wlan1` MAC
address.

After running `./upgrade.sh`, execute the following command:

```bash
sudo ./network-setup.sh --dhcp-reset
```

The `--dhcp-reset` flow removes the legacy service definition, reloads
`systemd`, and restores NetworkManager's default DHCP behavior for managed
interfaces. Once the cleanup finishes you can verify the new service with:

```bash
systemctl status wlan1-refresh.service
```

If the service is missing or disabled, run the interactive network configuration
again to reinstall it:

```bash
sudo ./network-setup.sh --interactive
```

This ensures the replacement `wlan1-refresh.service` unit is installed and the
node continues refreshing the secondary wireless interface after upgrades.
