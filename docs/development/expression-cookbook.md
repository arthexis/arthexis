# Expression Cookbook

This cookbook captures Arthexis-specific sigil expression patterns using the canonical uppercase pipeline form:

- **Root** and **action** heads are uppercase.
- Use `:` inside each segment.
- Join root and action segments with `|`.
- Prefer `[ROOT:...|ACTION:...]` over legacy dot/parenthesis forms for new work.

## OCPP charger and session queries

### Charger endpoint by hostname

| Legacy form | Canonical pipeline form |
| --- | --- |
| `[CP:hostname=SIM-CP-1.public_endpoint]` | `[CP:hostname:SIM-CP-1|FIELD:PUBLIC_ENDPOINT]` |

### Count active sessions

| Legacy form | Canonical pipeline form |
| --- | --- |
| `[SESS:status=ACTIVE.id=count]` | `[SESS:status:ACTIVE|COUNT:ID]` |

### Filter charger rows by status

| Legacy form | Canonical pipeline form |
| --- | --- |
| `[CP:status=AVAILABLE]` | `[CP:|FILTER:STATUS:AVAILABLE]` |

## Ownership-aware placeholders

Use placeholders when expressions are templated for owner-specific rendering.

| Use case | Example |
| --- | --- |
| Owner-scoped charger lookup | `[CP:owner__name:__OWNER_NAME__|FILTER:STATUS:AVAILABLE]` |
| Owner-scoped session count | `[SESS:owner__slug:__OWNER_SLUG__|COUNT:ID]` |
| Owner-scoped field projection | `[CP:owner__name:__OWNER_NAME__|FIELD:HOSTNAME]` |

## Request and system metadata lookups

| Purpose | Legacy form | Canonical pipeline form |
| --- | --- | --- |
| Request query value (`id_tag`) | `[REQ.get=id_tag]` | `[REQ|GET:ID_TAG]` |
| Request header (`Authorization`) | `[REQ.header=Authorization]` | `[REQ|GET:AUTHORIZATION]` |
| System version | `[SYS.VERSION]` | `[SYS|GET:VERSION]` |

## Migration guide: legacy dot/parenthesis to uppercase pipeline

When migrating existing expressions:

1. Uppercase the root and action head.
2. Move root filtering/selector data into the root payload (`ROOT:field:value`).
3. Move field projection/aggregate into the action payload (`ACTION:key`).
4. Keep the expression semantically equivalent and verify in both `admin` and `user` contexts as needed.

| Legacy expression | Pipeline expression | Notes |
| --- | --- | --- |
| `[CP:hostname=SIM-CP-1.public_endpoint]` | `[CP:hostname:SIM-CP-1|FIELD:PUBLIC_ENDPOINT]` | Field projection from one charger row. |
| `[CP:status=AVAILABLE.id=count]` | `[CP:status:AVAILABLE|COUNT:ID]` | Aggregate count over filtered rows. |
| `[REQ.get=id_tag]` | `[REQ|GET:ID_TAG]` | Request metadata retrieval. |
| `[SYS.VERSION]` | `[SYS|GET:VERSION]` | System metadata retrieval. |

## Policy reference by context

| Context | Allowed roots | Allowed actions |
| --- | --- | --- |
| `admin` | Any registered `SigilRoot` prefix (recommended as uppercase). | Any uppercase action token is accepted; prefer canonical actions for portability: `COUNT`, `FIELD`, `FILTER`, `GET`, `MAX`, `MIN`, `SUM`, `TOTAL`. |
| `user-safe` | Roots marked `is_user_safe=True`. | `COUNT`, `FIELD`, `FILTER`, `GET`, `MAX`, `MIN`, `SUM`, `TOTAL`. |
| `request` | `REQ` | `GET` |

## Contributor onboarding: adding roots/actions safely

When extending sigil capabilities:

1. **Register roots** in the `SigilRoot` model (built-ins are managed from `apps/sigils/builtin_policy.py`; model-backed roots come from fixtures and admin registration).
2. **Implement action behavior** in `apps/sigils/sigil_resolver.py` (pipeline parsing and resolver behavior).
3. **Document changes** in this cookbook and in `docs/development/sigil-script-command.md` examples.
4. **Test changes** in `apps/sigils/tests/` (parser coverage, policy context gating, and resolver behavior).
5. **Expose discoverability** in the admin Sigil Builder (`/admin/sigil-builder/`) by updating expression examples, policy context mapping, and filter metadata.

This keeps Arthexis operating as the integration pivot: expressions remain explicit, testable, and aligned with OCPP/admin workflows rather than ad-hoc side tooling.
