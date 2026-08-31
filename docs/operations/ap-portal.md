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
to the suite login at `http://arthexis.net:8888/login/` by default.
Override `--suite-login-scheme`, `--suite-login-host`, `--suite-login-port`, or
`--suite-login-path` only for gateways that use a different AP-side suite
address. Override `--authorized-redirect-delay-ms` when authorized clients
should wait for a different interval; the default is `3000` ms, and `0` redirects
immediately.

Successful registrations send an email to `DEFAULT_ADMIN_EMAIL` by default after
the consent and authorization files are written. Set
`ARTHEXIS_AP_REGISTRATION_EMAIL_RECIPIENT` or pass
`--registration-email-recipient` to notify a different address; leave both empty
to disable registration email. Notification failures are logged and recorded in
the AP activity log, but they do not revoke the client's authorization.

The portal does not allow any unauthenticated external destinations by default;
clients must accept the AP terms before HTTP/HTTPS forwarding is available.
Operators can add selected hosts, IP addresses, or CIDR networks with repeated
`--preauth-allow-host` arguments or the comma-separated
`ARTHEXIS_AP_PREAUTH_ALLOW_HOSTS` environment variable when a deployment needs a
pre-registration exception. The portal resolves hostnames when it syncs nftables
and allows unauthenticated HTTP/HTTPS forwarding only to those resolved
destinations.

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

Certbot can install that certificate, but ACME validation must be able to prove
control of the public `arthexis.net` name. HTTP-01 validation only works when
public DNS for `arthexis.net` resolves to this gateway and inbound port 80
reaches nginx here. Split local DNS is not enough; clients may resolve
`arthexis.net` locally, but Let's Encrypt still follows public DNS. When public
DNS points elsewhere, use DNS-01 with configured DNS provider credentials instead
of the nginx authenticator. A safe HTTP-01 readiness check is:

```bash
sudo certbot certonly --nginx --dry-run --non-interactive \
  --agree-tos --register-unsafely-without-email -d arthexis.net
```

The AP setup writes `server_name arthexis.net _;` by default so certbot's nginx
plugin can match the local server block once the public validation path is
correct. Override `SERVER_NAMES` only when the gateway serves a different local
hostname set.

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
  browser paths such as `/unknown/register/`, return the AP portal page instead of
  a bare 404. Hidden path probes and missing nested asset paths such as
  `/css/missing.css` still return 404.

If nginx validation fails, `setup_ap_portal.sh` restores the most recent
pre-portal nginx site backup before exiting.
