# Typing conventions for incremental Arthexis adoption

Use these conventions when tightening types in existing modules so static analysis and editor tooling gain signal without forcing a large rewrite.

## Preferred shapes

- Model external payloads with `TypedDict` when the code expects named keys from subprocesses, HTTP APIs, webhooks, or serializer-like data.
- Describe pluggable adapters and callback contracts with `Protocol` instead of `Any` or bare duck-typed comments.
- Prefer `X | None` for nullable values.
- Introduce small module-local type aliases for repeated shapes owned by that module.
- Narrow command and collection inputs to `Mapping[str, object]`, `Sequence[str]`, or similar concrete abstractions when callers do not require mutability.

## Adoption notes

- Replace `Any` only when it currently blocks a useful check or obscures a stable data shape.
- Keep aliases close to the owning module unless the same shape is shared broadly.
- Add or refresh docstrings only when they materially improve discoverability, capture non-obvious typing intent, or feed user-facing/generated help text. Prefer lean code over routine explanatory boilerplate.
- Favor incremental changes in lower-dynamic modules first, then expand app-by-app as coverage improves.
