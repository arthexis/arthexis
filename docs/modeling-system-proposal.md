# Proposal: Multi-Dimensional Modeling System for Arthexis Suite

## Executive summary
The Arthexis suite already treats every application interface (CLI, OCPP websocket, internal services) as a peer communication surface. To deepen this capability, we should formalize the modeling system as a **multi-dimensional interaction graph** where each model is a first-class communication dimension, and each surface is an adapter that can both originate and consume model interactions. This proposal defines a unified schema, routing, and governance layer that enables consistent cross-model communication without elevating any single channel as primary.

The approach centers on:
- A **Model Registry** describing dimensions, capabilities, and transformations.
- **Adapters** for each channel that emit/consume canonical events.
- **Transformers** that translate between dimensions with explicit contracts.
- **Interaction orchestration** for policy, routing, and collaboration patterns.
- **Observability and governance** to keep the system trustworthy and evolvable.

## Background and current challenge
We currently rely on several communication surfaces (e.g., CLI, OCPP websocket), each with its own schema and semantics. This makes multi-model interaction brittle. As no single channel is primary, the system must support multiple sources of truth while preserving consistency. It also needs to enable communication between models of different problem dimensions through defined transformations.

### Key problems to address
1. **Schema drift:** Each surface uses its own data structures, making translation ad hoc.
2. **Incomplete transformation contracts:** There is no canonical description of how a model in dimension A should map to dimension B.
3. **Missing routing rules:** It’s unclear how to detect the right target models and orchestrate a multi-model interaction.
4. **Observability gaps:** There is no unified trace of how a message traverses the system.

## Vision: the multi-dimensional interaction graph
We treat **each model as a dimension** and **each interface as an adapter** that can emit or consume **canonical interaction events**. Transformations between dimensions are defined in a shared registry as explicit contracts.

### Core terms
- **Dimension**: A problem space encapsulated by a model and its domain schema (e.g., OCPP control, CLI command execution, billing ledger).
- **Surface**: A communication interface (CLI, websocket, REST, message bus, etc.).
- **Adapter**: A surface-specific component that translates raw I/O into canonical events, and vice versa.
- **Transformation**: A deterministic or probabilistic mapping between dimensions.
- **Interaction**: A sequence of canonical events moving across dimensions and surfaces.

## Proposed system architecture

### 1. Model Registry (Source of truth)
The registry describes dimensions, schemas, and transformation contracts.

**Minimum registry record structure:**
- **Dimension ID** (stable, semantic name)
- **Schema definition** (JSON schema, Pydantic, or DSL)
- **Capabilities** (intents, operations, constraints)
- **Constraints** (pre/post conditions, invariants)
- **Transformations** (links to other dimensions)
- **Ownership and policy** (access control, required checks)

**Registry output:** a typed API that adapters and transformers can query to understand how to emit/consume events.

### 2. Canonical event format
Define a canonical event envelope shared across surfaces. Example fields:
- `event_id`
- `timestamp`
- `dimension_id`
- `intent`
- `payload` (dimension schema)
- `context` (correlation/trace IDs, origin surface, actor)
- `policy` (required validations, security expectations)

This format allows adapters to normalize events regardless of origin.

### 3. Surface adapters
Each surface becomes an adapter with a predictable contract:
- **Inbound parsing:** transform surface-specific input into canonical events.
- **Outbound rendering:** translate canonical events back into surface-specific responses.
- **Capability negotiation:** report supported intents and schema versions.

The CLI and OCPP websocket are just two adapters in this system, not privileged.

### 4. Transformation services
Transformers are isolated services (or modules) that map events between dimensions.

**Design principles:**
- **Explicit contracts:** each transformation is versioned and defined in the registry.
- **Determinism by default:** transformations should be deterministic unless explicitly modeled as probabilistic.
- **Loss and ambiguity handling:** transformations must declare fields they drop, derive, or approximate.
- **Bidirectional mappings when possible:** define forward and reverse transformations explicitly.

### 5. Interaction orchestration
An orchestration layer decides how canonical events flow between models and surfaces.

**Responsibilities:**
- **Routing:** select which dimensions/models should participate based on intent, context, and policy.
- **Collaboration patterns:** allow fan-out, consensus, or arbitration among models.
- **Conflict resolution:** merge and reconcile outputs when multiple models contribute.
- **Policy enforcement:** validate security and invariants before transitions.

### 6. Observability and governance
For a system with no primary channel, observability is the safety net.

**Requirements:**
- **Distributed tracing:** event IDs and correlation IDs across transformations.
- **Model-level telemetry:** performance, latency, error rates per dimension.
- **Versioned schema diffs:** change logs for transformations and dimension schemas.
- **Audit trail:** complete history for cross-surface interactions.

## Proposed workflow: end-to-end example
1. A CLI command emits a canonical event in the CLI dimension.
2. The orchestration layer maps the event to an OCPP dimension via registered transformers.
3. The OCPP adapter sends the event to the websocket surface.
4. Responses from the websocket are normalized back to the canonical format.
5. If the event also affects billing, a transformer maps to the billing dimension.
6. The orchestrator merges and responds to the origin surface.

## Modeling system improvements

### A. Dimension interface design
Each dimension defines:
- **Intent catalog:** enumerated intents a dimension can handle.
- **Input/Output schemas:** explicit, versioned, and validated.
- **Constraints:** invariants enforced by the domain model.

### B. Transformation contract specification
Provide a formal specification for transformations:
- **Inputs/outputs:** schema references by version.
- **Lossy fields:** flagged explicitly.
- **Preconditions:** required fields or state checks.
- **Postconditions:** resulting state or guarantees.
- **Version policy:** deprecation timeline and compatibility rules.

### C. Interaction policy framework
Define policy as code for routing and safety:
- **Safety rules:** block high-risk transitions without human approval.
- **Conflicts:** deterministic resolution or arbitration strategies.
- **Capability negotiation:** support partial or optional intents.

### D. Exploration vs. determinism
Support exploratory routes without destabilizing production:
- **Exploration channels:** allow experimentation with model routes under policy control.
- **Shadow evaluations:** run transformations in parallel without affecting state.
- **Confidence gating:** map uncertain transformations to human review.

## Implementation plan

### Phase 1: Foundation
- Create a canonical event schema (JSON schema or Pydantic model).
- Define the Model Registry structure and initial metadata.
- Wrap CLI and OCPP into adapters that produce canonical events.

### Phase 2: Transformations
- Implement transformers between CLI and OCPP dimensions.
- Add metadata on lossy fields and invariants.
- Build translation tests and schema validation.

### Phase 3: Orchestration
- Build the routing layer for fan-out and consensus.
- Introduce policy checks and conflict resolution strategies.
- Add a trace logging and telemetry pipeline.

### Phase 4: Governance
- Add registry versioning and change management tools.
- Track transformation drift and schema diffs.
- Publish a dashboard for interaction traces and model health.

## Metrics for success
- **Coverage:** % of events that are normalized through canonical format.
- **Transformation quality:** error rates and lossiness tracked by dimension pairs.
- **Latency:** time from origin surface to final response.
- **Model collaboration rate:** frequency of multi-model interactions.
- **Observability completeness:** trace coverage and audit integrity.

## Risks and mitigations
- **Complexity overhead:** Keep adapters thin and rely on shared schema tooling.
- **Schema drift:** enforce registry checks in CI and pre-merge validations.
- **Performance:** use async pipelines and batch transformations where possible.
- **Ambiguous transformations:** require explicit lossiness annotations and policy checks.

## Next steps
1. Choose a canonical event format and build a minimal Model Registry.
2. Prototype CLI ↔ OCPP transformation with a small intent set.
3. Add a test harness for transformation validation and logging.
4. Iterate with model owners to expand the dimension catalog.

---

### Appendix: Example canonical event envelope
```json
{
  "event_id": "evt_01H9M3KQ9XK5B28Z6P8R8W6J4T",
  "timestamp": "2024-04-01T12:34:56Z",
  "dimension_id": "ocpp.control",
  "intent": "start_transaction",
  "payload": {
    "charger_id": "CHG-001",
    "connector_id": 1,
    "id_tag": "RFID-123"
  },
  "context": {
    "origin_surface": "cli",
    "trace_id": "trace_88f8",
    "actor": "operator"
  },
  "policy": {
    "requires_approval": false,
    "security_level": "standard"
  }
}
```
