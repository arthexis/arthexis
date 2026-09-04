# Extension repositories

Arthexis can load optional Django applications from Git repositories checked out
under the repository-level `extensions/` directory. Extensions are code
availability; Suite Features remain the runtime capability gates.

Extensions may add capabilities to existing core applications. A specialized
printer extension, for example, should depend on `apps.printers` and contribute
its own Suite Feature rather than replacing the core printers application.

## Repository contract

Each extension repository must contain `arthexis-extension.toml` at its root.
A printer-driver extension could look like this:

```toml
[extension]
name = "printer-zebra"
repository = "arthexis/arthexis-printer-zebra"
django_apps = ["arthexis_printer_zebra"]
requires_apps = ["apps.printers"]
feature_packs = ["printer_workflows"]

[[suite_features]]
slug = "zebra-label-printing"
display = "Zebra Label Printing"
main_app = "printers"
summary = "Adds Zebra label printing to the core printers app."
enabled_by_default = false
```

`django_apps` contains importable Django application entries. `requires_apps`
declares apps that must already be available in the suite. `feature_packs`
documents app-selection contracts.

Structured `[[suite_features]]` entries are synchronized into the normal
`features.Feature` catalog. `main_app` may name an existing core application such
as `printers`, `ocpp`, `energy`, or `sensors`; extensions therefore extend core
capabilities instead of replacing their owning apps. New extension features are
disabled by default unless `enabled_by_default = true` is explicitly declared.
Subsequent syncs update descriptive metadata but preserve the operator's current
enabled/disabled choice.

An installed extension repository is added to `sys.path` before Django finalizes
`INSTALLED_APPS`. Extension apps can therefore provide normal Django models,
migrations, admin registrations, management commands, templates, and static
assets without copying code into the main Arthexis repository.

## Declaring extensions

Persistent extension selections live in `extensions/extensions.toml`:

```toml
[extensions]
printer-zebra = "arthexis/arthexis-printer-zebra"
another = "example/arthexis-another"
```

Deployments may also supply comma-, semicolon-, or whitespace-separated
repositories through `ARTHEXIS_EXTENSIONS` or `ARTHEXIS_EXTENSION_REPOS`.
Environment declarations are combined with the file declarations.

Short names use the default GitHub owner and the `arthexis-` repository prefix.
For example, `printer-zebra` resolves to `arthexis/arthexis-printer-zebra`.

## Commands

List configured and checked-out extensions:

```bash
.venv/bin/python manage.py extensions list
```

List repositories published by the default GitHub owner with the
`arthexis-extension` topic:

```bash
.venv/bin/python manage.py extensions available
```

Install one or more extensions and persist them to `extensions/extensions.toml`:

```bash
.venv/bin/python manage.py extensions install printer-zebra another
```

Clone every missing declaration, fast-forward existing managed checkouts, and
synchronize their Suite Feature definitions:

```bash
.venv/bin/python manage.py extensions sync
```

To synchronize feature definitions from already-installed extensions without
updating Git checkouts:

```bash
.venv/bin/python manage.py extensions sync-features
```

The commands use normal Git HTTPS checkouts and therefore respect the host's Git
credential configuration. After adding or updating an extension that changes
Django apps or migrations, restart Arthexis and run the normal migration flow.

## Feature enablement

Installing an extension does not imply that every capability it implements is
enabled. Extensions should model user-facing capabilities as Suite Features and
gate their behavior with the existing feature APIs. Device-specific requirements
should use Node Features.

This separation keeps repository management deterministic:

1. the checkout determines whether implementation code is available;
2. Django app loading determines whether the extension participates in the node;
3. Suite Feature and Node Feature state determines whether a capability is active.

Feature toggles must not clone, delete, or update Git repositories.
