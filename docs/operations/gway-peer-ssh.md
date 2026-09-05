# Direct GWAY peer SSH

Fresh GWAY images use key-only recovery SSH for the `arthe` account. They also
carry a charger-facing `eth0` profile whose default address is
`192.168.129.10/24`. That address is the canonical wired management target when a
reserved hostname such as `gway-004` is not resolvable.

## Burn with the parent operator key

Use the canonical wrapper from the repository root:

```bash
python scripts/gway_burn.py --reserve-number 4
```

The wrapper selects the recovery public key in this order:

1. `IMAGER_GWAY_RECOVERY_AUTHORIZED_KEY_FILE`
2. `~/.ssh/id_ed25519.pub`
3. `~/.ssh/id_ecdsa.pub`
4. `~/.ssh/id_rsa.pub`

Before delegating to `manage.py imager gway-burn`, it prints the selected key
source and its OpenSSH-style SHA256 fingerprint. It never prints private key
material and it does not introduce a shared/default password. Explicit
`--recovery-authorized-key-file`, `--recovery-authorized-key`, or
`--skip-recovery-ssh` options still take precedence.

## Direct-cable a parent GWAY to the new node

Do not leave both machines configured as `192.168.129.10`. For example, when
`gway-001` normally owns `.10`, temporarily move the parent's `eth0` to `.1`
before connecting the Ethernet cable:

```bash
sudo nmcli con add type ethernet ifname eth0 con-name arthexis-gway-peer \
  ipv4.method manual ipv4.addresses 192.168.129.1/24 \
  ipv4.never-default yes ipv6.method disabled
sudo nmcli con up arthexis-gway-peer
```

The new node remains at `192.168.129.10/24`, so the parent and child now occupy
non-conflicting addresses on the same directly connected subnet.

If `.1` is already used in the deployment, choose another unused address in the
same subnet for the parent. The parent address is only a provisioning/handoff
address; `.10` remains the field default expected by the charger-facing setup.

## Connect and verify

Use the private key corresponding to the public key reported during the burn:

```bash
ssh -i ~/.ssh/id_ed25519 arthe@192.168.129.10
```

The imager already provides a combined SSH and application reachability check:

```bash
python manage.py imager test-access \
  --host 192.168.129.10 \
  --ssh-user arthe \
  --ssh-key ~/.ssh/id_ed25519
```

By default this checks TCP/22, key authentication, and HTTP on port 8888. This
IP-based route is the supported fallback when `gway-004`/mDNS/DNS resolution is
not available.

After provisioning, restore the parent's normal charger-facing connection before
putting it back in service. For a host using the default profile name:

```bash
sudo nmcli con down arthexis-gway-peer
sudo nmcli con up arthexis-charger-eth0
```

Delete the temporary profile when it is no longer needed:

```bash
sudo nmcli con delete arthexis-gway-peer
```

## Override the new GWAY charger-facing address

The field default is `192.168.129.10/24`. An image build can override it without
creating a separate NetworkManager keyfile:

```bash
IMAGER_CHARGER_ETH0_ADDRESS=192.168.129.20/24 \
  python scripts/gway_burn.py --reserve-number 4
```

The generated profile remains manual IPv4, pinned to `eth0`, with
`never-default=true` and IPv6 disabled. An explicitly copied host profile pinned
to `eth0` still replaces this generated default entirely.
