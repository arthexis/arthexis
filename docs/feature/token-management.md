# Self-service token management

Arthexis now includes a self-service flow for operator-managed service tokens in Django admin so integrations can stay connected through the suite instead of external side systems.

## Roles and authorization

Use Django permissions to grant token lifecycle responsibilities:

- `apis.manage_service_tokens`: create/request, revoke, and rotate scoped tokens.
- `apis.reveal_service_token_secret`: reveal newly created or rotated secrets one time.

Recommended role split:

- **Operators** receive both permissions for day-to-day integration credential workflows.
- **Auditors** receive read-only admin access to `Service Token` and `Service Token Event` models.

## Operational guardrails

- Expiry is required and policy-limited to **90 days**.
- Scopes are explicitly selected at issuance time and stored with the token.
- Secrets are shown once in the reveal view and are not retrievable afterwards.
- Revoke and rotate flows require confirmation and ask operators to record impact notes.
- Every lifecycle action (create, reveal, revoke, rotate) generates a `Service Token Event` entry with actor and details.

## Admin workflow

1. Open **Admin → API Explorer → Service Tokens**.
2. Use **Create service token** and set scopes + expiry.
3. Copy the secret from the one-time reveal screen.
4. Use **Revoke** or **Rotate** endpoint actions when credentials need to be retired.
5. Review **Service Token Events** for actor-linked audit trails.
