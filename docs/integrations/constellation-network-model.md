# Constellation Network Model

Arthexis Constellation should not make a third-party mesh overlay or VPN
control plane the default coordination model for nodes. Operators may still use
VPNs, tunnels, private links, or local overlays as transport, but the suite
should not require a Tailscale-like model to identify, authorize, or coordinate
the fleet.

## Decision

The Constellation model is application-first:

- Node identity lives in Arthexis models, enrollment records, certificates,
  tokens, and audit trails.
- Authorization is expressed through suite roles, scopes, staff permissions,
  and per-endpoint contracts.
- Node reachability is treated as a transport concern that can vary by site,
  role, network policy, and incident state.
- The suite remains useful when a node is offline, behind NAT, running on a
  local AP, or connected through a site-specific private network.

The inverse model, where every node must join a shared overlay before the suite
can reason about it, would make the overlay the real source of truth. That is
not the shape we want for Constellation.

## Why not overlay-first

### 1. It weakens Arthexis as the system of record

Constellation needs durable records for node role, enrollment lifecycle,
operator actions, charger control authority, and release state. If fleet
coordination depends on an external mesh membership list, operators must
reconcile two authority planes:

- the overlay says whether a device exists and can route packets;
- Arthexis says what the device is, what it is allowed to do, and which events
  explain its state.

That split is easy to tolerate for ad hoc remote access, but it is a poor
foundation for product behavior, auditability, and support.

### 2. It hides deployment failures behind network magic

Field nodes need to work across simple LANs, charger-facing networks, local AP
recovery paths, production Watchtower deployments, and offline maintenance
states. An always-on overlay can make a broken site look healthy because the
operator can still reach the node through a side channel. The suite should
surface the real topology and failure layer instead:

- charger-facing HTTP/WebSocket reachability;
- node enrollment and token state;
- local service health;
- upstream internet state;
- role-specific routes such as Control, Satellite, and Watchtower paths.

Debugging should answer "what part of this Constellation path is broken", not
"why did the overlay stop making this reachable".

### 3. It makes offline and recovery behavior second-class

GWAY and field-node workflows often need local-first behavior: an AP portal,
physical-device recovery, RFID or LCD feedback, SD-card preparation, and
operator actions while internet access is unstable. These are not exceptions to
the Constellation model; they are core operating modes.

If the default model assumes every node is online in a mesh, offline recovery
becomes an afterthought. The suite should instead preserve a clear path for:

- local node bootstrapping;
- delayed sync after reconnection;
- explicit manual tasks;
- local logs and health reports;
- recovery keys and site-specific operator access.

### 4. It couples product security to a transport vendor

VPN and mesh tools can be useful, but they should not define Constellation's
authorization semantics. Product security should stay in suite-owned concepts:

- node enrollment records;
- token scope such as `mesh:read` or `ocpp:control`;
- canonical staff groups and permissions;
- signed requests or certificates where needed;
- event logs that explain who did what and why.

Transport encryption is not a substitute for product authorization. A node that
can route to the suite should still need the right suite identity and scope.

### 5. It creates a support and portability tax

Constellation needs to run on development laptops, Raspberry Pi field nodes,
site-local networks, production hosts, and customer-controlled environments.
Requiring a particular mesh overlay adds an extra install, account, ACL, key
rotation, and incident-response surface before the suite can operate.

That tax is acceptable when a site chooses it as infrastructure. It is not
acceptable as the product's default assumption.

## Preferred model

Build Constellation around explicit application contracts:

- **Enrollment first**: a node becomes known through suite enrollment, not by
  appearing in a network overlay.
- **Role-aware behavior**: Terminal, Control, Satellite, and Watchtower nodes
  expose different capabilities and guardrails.
- **Transport adapters**: LAN, AP-side access, reverse tunnels, cloud ingress,
  VPNs, and future relays are interchangeable reachability options.
- **Auditable actions**: commands, assignments, charger control, upgrades, and
  release operations should leave suite-visible records.
- **Graceful degradation**: offline nodes remain visible as stale, waiting, or
  recovery-required instead of disappearing from the control model.

This keeps network transport replaceable while preserving a single product
authority model.

## Acceptable overlay use

Avoiding a Tailscale-like default does not mean banning overlays. It is
reasonable to use them when they are:

- operator-managed infrastructure rather than required suite state;
- limited to remote shell, support, monitoring, or emergency access;
- documented as one transport path among several;
- backed by the same suite-side enrollment, authorization, and audit checks as
  any other path;
- removable without changing node identity or product permissions.

In short: an overlay may carry packets, but it should not decide what a
Constellation node is or what it may do.

## Design consequences

New Constellation features should pass this checklist:

1. Can the feature still represent a node that is offline or only locally
   reachable?
2. Is node identity stored in Arthexis rather than inferred from network
   membership?
3. Are permissions checked at the suite/API layer even if the transport is
   private?
4. Does the operator see the real transport path used for an action?
5. Can the same workflow run through LAN, AP-side recovery, production ingress,
   or a site-chosen VPN without changing the product model?

If the answer to any of these is no, the feature is probably depending on an
overlay as architecture instead of using it as transport.
