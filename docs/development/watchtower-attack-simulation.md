# Watchtower internet attack simulation playbook

Use this playbook to run controlled internet-style attacks against a Watchtower node in a lab so you can validate that basic defenses are working.

## Scope and goals

This guide focuses on high-frequency, low-complexity attacks that are often seen first on exposed services:

1. Reconnaissance and service enumeration.
2. Credential stuffing and brute-force logins.
3. HTTP flood and slow-client exhaustion.
4. Malformed API/WebSocket payloads.
5. TLS and header misconfiguration checks.

Success means attacks are blocked or throttled, logs are generated, and the node stays available for legitimate traffic.

The broader suite goal remains the same here as elsewhere: operators should rely on developed and tested defenses and integrations rather than recipe-like behaviors or custom wiring that force them into programming roles. **SIGILS** remain acceptable for straightforward defaults and basic templating where no control flow is required.

## Safety rules (required)

- Run only against infrastructure you own or have written permission to test.
- Isolate the Watchtower in a test VLAN or cloud project.
- Use throwaway credentials and synthetic data.
- Predefine stop conditions (CPU > 90% for 5 min, API p95 > 2s, packet loss > 5%, etc.).
- Keep an observer host that only monitors and records metrics.

## Lab topology

- **Target:** One Watchtower node with the same reverse proxy, firewall, and auth settings as production.
- **Attack host:** A separate VM/container that generates traffic.
- **Observer:** Prometheus/Grafana + central log sink (or journalctl export) for visibility.

Recommended minimum telemetry:

- Request rate, 4xx/5xx split, and endpoint latency.
- CPU, memory, file descriptors, and socket states.
- Login failures by source IP and account.
- Reverse-proxy deny/limit logs.

## Baseline before attacks

Record a clean baseline for 10 to 15 minutes:

```bash
curl -fsS https://WATCHTOWER_HOST/health/
python manage.py check
```

Keep this baseline to compare during each simulation.

## Attack simulations

Run one family of attacks at a time. Reset and verify normal behavior between scenarios.

### 1) Recon and exposure scan

```bash
nmap -sS -sV -Pn WATCHTOWER_IP
nmap --script vuln -Pn WATCHTOWER_IP
```

Expected:

- Only required ports are open (typically 443, maybe 80 for redirect).
- Unnecessary services are absent.
- No known high-severity findings from default `vuln` scripts.

### 2) Basic auth brute-force simulation

Use a disposable account and small dictionaries first.

```bash
hydra -l admin -P ./wordlists/top-100.txt WATCHTOWER_HOST https-post-form \
  "/admin/login/:username=^USER^&password=^PASS^:F=Please enter the correct"
```

Expected:

- Rate limiting triggers quickly.
- Source IP gets delayed or blocked.
- Repeated failures are logged with enough context to investigate.

### 3) HTTP request flood

```bash
hey -z 60s -q 20 -c 50 https://WATCHTOWER_HOST/
```

Expected:

- Reverse proxy enforces request limits.
- App remains responsive for low-rate valid requests.
- No worker crash/restart loop.

### 4) Slow-client / slowloris behavior

```bash
slowhttptest -X -c 500 -r 200 -w 10 -y 20 -z 30 -u https://WATCHTOWER_HOST/
```

Expected:

- Connection/read timeouts close abusive sockets.
- File descriptor usage stays bounded.

### 5) Malformed and oversized payloads

```bash
curl -X POST https://WATCHTOWER_HOST/api/endpoint \
  -H 'Content-Type: application/json' \
  --data-binary @malformed.json
```

Also test oversized bodies and invalid UTF-8.

Expected:

- 4xx responses without 5xx spikes.
- Exceptions are handled safely (no stack traces to clients).

### 6) TLS posture checks

```bash
nmap --script ssl-enum-ciphers -p 443 WATCHTOWER_IP
testssl.sh WATCHTOWER_HOST
```

Expected:

- TLS 1.2+ only.
- Strong ciphers and valid certificate chain.
- HSTS enabled when operationally appropriate.

## Resilience scorecard

Track each scenario with pass/fail and evidence:

- **Prevent:** blocked, rate-limited, or challenged.
- **Detect:** alert/log generated with source context.
- **Sustain:** service quality remained within SLO.
- **Recover:** node returned to baseline after test stop.

A Watchtower node is "basic-attack resilient" only if all four categories pass for every scenario.

## Hardening actions when tests fail

1. Restrict ingress to required ports and trusted ranges where possible.
2. Add/adjust reverse-proxy rate limits per IP and per endpoint.
3. Enforce MFA for privileged accounts and lockout/backoff policies.
4. Tighten Django settings (`SECURE_*`, cookie flags, CSRF, allowed hosts).
5. Set upstream and app timeouts to defeat slow-client abuse.
6. Add WAF rules for obvious malformed payload signatures.
7. Ensure logs are centralized and alerts trigger on brute-force thresholds.

## Suggested cadence

- Run lightweight simulations weekly (recon + brute-force + small flood).
- Run full suite monthly or before every release.
- Treat any failed check as a release blocker for internet-exposed Watchtower roles.

## Codex automation

For an agent-driven implementation of this playbook, see the companion skill proposal in [`watchtower-codex-skill-proposal.md`](./watchtower-codex-skill-proposal.md).
