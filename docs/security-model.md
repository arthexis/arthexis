# Ownership and security model

This project standardizes staff authorization around five canonical security groups while still allowing security groups and avatars to model ownership for user-facing records. `Ownable` in `apps/core/models/ownable.py` provides the shared user/group ownership layer, and profile-style models extend that pattern with avatar ownership when a specific avatar must hold the record.

## Security group model

Arthexis centers staff permissions around five canonical security groups:

1. **Site Operator** — staff with physical access to node hardware.
2. **Network Operator** — staff who can coordinate or execute multi-node actions.
3. **Product Developer** — staff who can submit codebase changes.
4. **Release Manager** — staff who can merge changes and authorize releases.
5. **External Agent** — minimal-access staff accounts for third-party collaborators.

These groups are the default staff grouping model for the suite. App-specific staff access should be expressed by assigning permissions to one or more of these groups instead of creating a new staff taxonomy for each subsystem. In the Django admin security-group changelist, the canonical five should be shown distinctly from other user-facing security groups so administrators can tell apart staff authorization groups from domain ownership groups.

### Default staff assignments

The suite now treats a few staff accounts as having baseline canonical memberships by default:

- **`admin`** should always include **Site Operator**.
- **`arthexis`** should always include **Network Operator**, **Product Developer**, and **Release Manager**.
- **Other staff accounts** default to **External Agent** unless a creation flow explicitly assigns different groups.

These defaults provide a common baseline; they do not prevent administrators from adding more canonical or user-facing security groups when a workflow needs them.

### User-facing security groups

Security groups still matter outside the five staff defaults. Non-canonical security groups remain valid for user-facing access and ownership scenarios, including object ownership, workflow scoping, branding, and app-specific sharing. In other words:

- **Canonical staff groups** answer “what staff authority does this account have by default?”
- **Other security groups** answer “which set of users should be treated as the owner or audience for this record?”

Avatars are not a general-purpose permission system. They are ownership identities for records that need a specific avatar rather than a whole user account or security group.

## Ownership model

Owner-capable records in Arthexis can be assigned in three main ways:

- **A specific user** owns the record directly.
- **A security group** owns the record so any user in that group acts as an owner.
- **A specific avatar** owns the record when the workflow needs an avatar identity rather than general account permissions.

`Ownable` itself provides the shared user/group ownership mechanics. Profile-style models extend that base with avatar ownership, and user profile resolution treats avatar-owned records as available to the user holding that avatar.

## Key behaviors

- **Mutually exclusive owners:** An ownership-enabled record must be linked to exactly one owner identity in the fields it exposes. Base `Ownable` models allow either a user or a security group. Profile-style models add avatar as a third mutually exclusive option.
- **Common helpers:** Each ownership-enabled model exposes `owner` and `owner_display()` for human-readable labels, plus `owner_members()` where group membership expansion is relevant.
- **Sigils:** When resolving sigils with the object supplied as `current`, `[OBJECT.OWNER]` returns the display value of the owner and `[OBJECT.OWNERS]` returns a JSON array of usernames. Group-owned records expand to all group members; user-owned records return the single user.

## Admin experience

- All `Ownable` admin classes inherit `OwnableAdminMixin`, which normalizes ownership fieldsets and applies validation before saving.
- Security groups should make the canonical five visibly distinct from other user-facing security groups in admin listings so staff-role groups are easy to recognize.
- User and Security Group change forms show an **Owned objects** section summarizing:
  - Directly owned objects.
  - Objects owned through a related security group or through group members, with the source called out.

## Developer guidance

1. **Make a model owner-capable**
   - Inherit from `Ownable` when user/group ownership is sufficient.
   - Extend the ownership pattern with an avatar field when the model must be owned by a specific avatar.
   - Set `owner_required = True` when the model must always be linked to an owner identity.
   - Keep business-specific validation in `clean()` but rely on the shared ownership checks instead of duplicating them.
2. **Staff authorization**
   - Prefer the five canonical staff security groups for staff-facing permissions.
   - Prefer non-canonical security groups only when modeling user-facing ownership or audience boundaries.
3. **Surface ownership in staff tooling**
   - Use `get_owned_objects_for_user()` and `get_owned_objects_for_group()` to summarize ownership for dashboards or reports.
4. **Use sigils for templates and notifications**
   - When rendering text that has access to the current owner-capable instance, `[OBJECT.OWNER]` and `[OBJECT.OWNERS]` provide owner-aware placeholders without custom code.
