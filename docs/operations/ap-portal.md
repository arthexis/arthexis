# AP Portal Recovery

The AP portal is the simple Python welcome and consent screen for the
`arthexis-1` access point. It runs as `arthexis-ap-portal.service` on
`127.0.0.1:9080` and nginx exposes it on port 80 and, when local certificates
exist, port 443.

Use this runbook when AP clients see the Django suite page instead of the AP
portal, or when `http://10.42.0.1/health` does not return portal JSON.

## Local Prototype

From the repository root:

```bash
python scripts/ap_portal_server.py --bind 127.0.0.1 --port 9080 --skip-firewall-sync
```

Then open:

```text
http://127.0.0.1:9080/
http://127.0.0.1:9080/health
http://127.0.0.1:9080/api/status
```

`--skip-firewall-sync` is for development only. Production AP installs must let
the portal synchronize nftables authorization rules. In this local-only mode,
loopback clients use a deterministic development MAC (`02:00:00:00:00:01`) so
the consent flow can be previewed from a browser without an AP neighbor table.
Authorized clients wait three seconds on the portal status message, then redirect
to the AP guest gallery at `http://arthexis.net:8888/gallery/ap/` by default.
Override `--suite-login-scheme`, `--suite-login-host`, `--suite-login-port`, or
`--suite-login-path` only for gateways that use a different AP-side suite
address. Override `--authorized-redirect-delay-ms` when authorized clients
should wait for a different interval; the default is `3000` ms, and `0` redirects
immediately.

Devices that should skip AP registration can be placed in
`.state/ap_portal/trusted_macs.txt`, one MAC address per line. Lines may include
comments or a short label after the MAC, for example:

```text
2c:cc:44:4d:1a:c5 ps4
# aa:bb:cc:dd:ee:ff reserved
```

Trusted devices are treated as authorized for the portal and nftables sync, but
they are kept separate from `authorized_macs.txt` so consent-backed
authorizations remain distinguishable from local operator allow-list entries.

Local HTTPS requires a certificate that is valid for `arthexis.net`. The setup
script looks for `/etc/letsencrypt/live/arthexis.net/fullchain.pem` and
`/etc/letsencrypt/live/arthexis.net/privkey.pem` by default before adding a
port 443 nginx server block; override `DEFAULT_CERT_PATH` and `DEFAULT_KEY_PATH`
only when a gateway uses a different certificate location.

## Gateway Recovery

On the gateway device:

```bash
cd /path/to/arthexis
sudo ./scripts/setup_ap_portal.sh
systemctl status arthexis-ap-portal.service --no-pager
curl -i http://127.0.0.1:9080/health
curl -i http://127.0.0.1:9080/api/status
curl -i http://10.42.0.1/health
```

Expected results:

- `arthexis-ap-portal.service` is active.
- Local port `9080` returns `{"ok": true}` from `/health`.
- AP-facing port `80` returns the same portal health JSON through nginx.
- `http://10.42.0.1/` shows the AP consent page headed `AP activity is monitored`.
- Captive-portal probe paths such as `/connecttest.txt`, plus nested unknown
  browser paths such as `/soul/register/`, return the AP portal page instead of
  a bare 404. Hidden path probes and missing nested asset paths such as
  `/css/missing.css` still return 404.

If nginx validation fails, `setup_ap_portal.sh` restores the most recent
pre-portal nginx site backup before exiting.

## Registration Template Packaging

If `/soul/register/` returns a server error while other suite pages render, check
that `apps.souls` is present under `[tool.setuptools.package-data]` in
`pyproject.toml`. The registration flow renders templates from
`apps/souls/templates/souls/`, so release wheels must include that tree.
