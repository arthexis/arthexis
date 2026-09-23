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

Both services use:

```text
GWAY_CACHE_DIR=/var/lib/gway/cache
```

so named scopes, OAuth grants, token verifier state, and remote account state
survive application upgrades and service reinstalls.

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
