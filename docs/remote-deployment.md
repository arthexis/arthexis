# Watchtower remote deployment

Watchtower exposes remote Gway access at:

```text
https://remote.arthexis.com
```

Arthexis owns production composition. Gway owns the generic service, security,
DNS, TLS, nginx, and MCP/OAuth primitives.

## Composition

The deployment is split into independent child recipes:

```text
deploy/watchtower.rx
    -> deploy/arthexis.rx
    -> deploy/remote-expose.rx

deploy/remote.rx
    -> chatgpt-logs policy
    -> mcp-server
    -> remote-auth

deploy/remote-dns.rx
    -> one-time DNS bootstrap
```

The recurring service deployment runs `deploy/remote.rx` before public
exposure. It applies the checked-in `chatgpt-logs` scope and reconciles both
systemd services against stable loopback endpoints:

```text
mcp-server   127.0.0.1:8000
remote-auth  127.0.0.1:8001
```

Both services resolve the project semantic value:

```text
cache_dir = /var/lib/gway/cache
```

from `[tool.gway.variables]` in `pyproject.toml`. The service definitions do
not transport a literal Gway environment variable. Because both fresh Gway
processes bootstrap the same project semantic configuration, named scopes,
OAuth grants, token verifier state, and remote account state survive
application upgrades and service reinstalls.

## Public edge

The public nginx topology is supplied by Gway's generic `web/remote` sampler.
It exposes the two loopback services through one TLS origin:

```text
remote.arthexis.com/mcp              -> 127.0.0.1:8000
remote.arthexis.com OAuth/account UI -> 127.0.0.1:8001
```

Watchtower reuses the public IPv4 already serving `arthexis.com`. The DNS A
record is created only when `remote.arthexis.com` does not resolve.
Subsequent deployments do not recreate DNS: `deploy/remote-expose.rx`
verifies the expected A record before invoking the TLS/nginx exposure recipe.

## Authorization policy

The production remote logging policy is reviewable source:

```text
deploy/mcp-scopes.toml
```

It grants only:

```text
log.sources
log.read
log.tail
log.search
```

and grants no environment variables.

Recurring deployment never creates native Gway bearers, OAuth access tokens,
refresh tokens, or account-linking credentials. O8 owns the one-time production
credential/linking step.

## Acceptance

`scripts/verify_remote_deployment.py local` verifies that both loopback
listeners become reachable before public exposure.

After DNS/TLS/nginx deployment,
`scripts/verify_remote_deployment.py public` validates:

- RFC 9728 protected-resource metadata;
- OAuth authorization-server metadata;
- PKCE S256 advertisement;
- protocol-appropriate behavior for authorization and token endpoints;
- HTTP 401 for unauthenticated `/mcp`;
- the MCP `WWW-Authenticate` challenge pointing to the exact protected-resource
  metadata URL.

The GitHub Actions deployment also confirms both systemd services are active.

## Deployment safety

The recurring path is intentionally convergent:

- scope application replaces the named scope transactionally;
- service install/restart reconciles stable service identities;
- DNS creation is isolated to first-time bootstrap;
- recurring DNS handling is verification-only;
- nginx changes use Gway's transactional rollback journals;
- certificate renewal uses the existing Gway/Certbot exposure lifecycle;
- no recurring step issues production credentials.


## O8A production preflight

Before issuing the one-time linking bearer, run the safe preflight against the
durable production security state:

```bash
cd /var/lib/gway/projects/arthexis
sudo .venv/bin/python -m gway ./deploy/remote-preflight.rx
```

The preflight uses the same project-level semantic `cache_dir`; it does not set
or require a literal cache environment variable. It emits only safe metadata.
It must show the `chatgpt-logs` scope,
the current safe token list, and healthy status for both `mcp-server` and
`remote-auth`. It never creates or prints a bearer secret.

The production MCP wrapper deliberately delegates to Gway's maintained
`mcp/server` sampler and supplies only deployment context:

```text
host: 127.0.0.1
port: 8000
route: /mcp
endpoint: https://remote.arthexis.com/mcp
```

Arthexis does not copy or own the FastMCP companion implementation.


The normal operator path is the **Watchtower Recovery** workflow. Choose
`diagnose` for the existing sanitized host report, or `checkpoint` to run
that report and then perform the remote preflight plus local/public OAuth/MCP
acceptance. Checkpoint is safe to run repeatedly and never issues credentials.


## DNS credentials

Watchtower does not require GoDaddy credentials to be copied into GitHub
Actions. The DNS recipe remains credential-free: Gway resolves semantic
`pat`, `api_key`, and `api_secret` values through its configured bindings.
The production host uses the persistent secret backend:

```text
/etc/gway/secrets/dns/godaddy/pat
/etc/gway/secrets/dns/godaddy/key
/etc/gway/secrets/dns/godaddy/secret
```

A PAT is preferred when present; otherwise the key/secret pair is used. The
secret-store root and file layout are physical backend details rather than part
of the DNS recipe API. These host secrets survive normal Arthexis/Watchtower
redeployments.
