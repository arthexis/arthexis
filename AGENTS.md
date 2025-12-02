# Agent Guidelines

- When adding or modifying migrations, always provide full dotted module paths to callables passed to `import_callable` (e.g. `apps.sigils.fields.SigilShortAutoField`).
- Prefer referencing stable migration helpers (such as modules under `apps.*.migration_utils`) instead of application model modules, and supply alias maps when relocating callables so old paths remain usable.
- If you encounter a shorthand path in an existing migration, rewrite it to the correct dotted path before merging.
