# OCPP Collector deployment

The OCPP Collector is the central repository/API used by OCPP Forwarders and
Arthexis consumers. It is intentionally separate from the satellite playbook.

Run it with:

```bash
ansible-playbook -i <inventory> ansible/playbooks/collector.yml
```

## PostgreSQL reuse

This role **does not install PostgreSQL**.

By default, `ocpp_collector_pg_host` is empty. The role then checks for an
existing local PostgreSQL Unix socket at:

```
{{ ocpp_collector_pg_local_socket_dir }}/.s.PGSQL.{{ ocpp_collector_pg_port }}
```

and uses peer authentication. In this mode the service user and database role
must have the same name.

For an explicitly selected PostgreSQL endpoint, set for example:

```yaml
ocpp_collector_pg_host: "postgres.internal"
ocpp_collector_pg_port: 5432
ocpp_collector_pg_admin_user: "postgres"
ocpp_collector_pg_admin_database: "postgres"
ocpp_collector_pg_admin_password: "{{ vault_pg_admin_password }}"
ocpp_collector_db_password: "{{ vault_collector_db_password }}"
```

Explicit settings always take precedence. If local detection fails, the role
fails rather than installing PostgreSQL or guessing a remote endpoint.

## PostgREST

The role installs the pinned upstream static PostgREST binary for x86_64 or
ARM64, verifies its SHA-256 digest, and exposes only the PostgreSQL `api`
schema. PostgREST listens on loopback by default.

Client authentication and per-satellite authorization are deliberately left to
the next implementation chunk.

## Reverse proxy

`ocpp_collector_proxy_mode` defaults to `auto`. Auto mode reuses an existing
nginx installation and never installs nginx.

- `auto`: require detected nginx and configure the Collector site.
- `nginx`: explicitly use an existing nginx installation.
- `none`: leave external exposure/TLS to another proxy layer.

When `ocpp_collector_tls_certificate` and
`ocpp_collector_tls_certificate_key` are supplied, the nginx site listens
with TLS. When they are omitted, the role creates the HTTP virtual host so an
existing external TLS layer or certificate workflow can own termination.

## Authentication and satellite enrollment

PostgREST requires JWT authentication. Supply a signing secret of at least 32
characters through Vault or host variables:

```yaml
ocpp_collector_jwt_secret: "{{ vault_ocpp_collector_jwt_secret }}"
ocpp_collector_satellites:
  - id: gway-004
  - id: gway-007
```

The enrollment list is authoritative. Satellites omitted from the next
convergence are retained historically but marked disabled, so their existing
JWTs can no longer write data. A satellite's JWT identity is its stable
`satellite_id`; the CSMS `source_id` remains a separate data-store epoch.

The role creates two authenticated API roles:

- `ocpp_forwarder`: SELECT/INSERT/UPDATE only, restricted by PostgreSQL RLS to
  the JWT's enabled `satellite_id`.
- `arthexis_reader`: fleet-wide SELECT only.

Anonymous users receive no table privileges.

### Issuing tokens

The role installs `/usr/local/bin/ocpp-collector-token`. Keep the signing
secret out of shell history by supplying it through the environment or a
secret-management wrapper.

Issue a Forwarder token:

```bash
OCPP_COLLECTOR_JWT_SECRET="$SECRET" \
  ocpp-collector-token forwarder gway-004
```

Issue an Arthexis read-only token:

```bash
OCPP_COLLECTOR_JWT_SECRET="$SECRET" \
  ocpp-collector-token reader
```

Tokens expire after 90 days by default; override `--ttl` when a different
lifetime is required. Disabling a satellite in Ansible takes effect at the
database policy layer independently of token expiration.
