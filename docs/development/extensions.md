# Extension repositories

Arthexis can load optional Django applications from Git repositories checked out
under the repository-level `extensions/` directory. Extensions are code
availability; Suite Features remain the runtime capability gates.

## Repository contract

Each extension repository must contain `arthexis-extension.toml` at its root.
A minimal extension looks like this:

```toml
[extension]
name = "printers"
repository = "arthexis/arthexis-printers"
django_apps = ["arthexis_printers"]
requires_apps = ["apps.core"]
feature_packs = ["printer_workflows"]
suite_features = ["printer-workflows"]
```

`django_apps` contains importable Django application entries. `requires_apps`
declares apps that must already be available in the suite. `feature_packs` and
`suite_features` document the extension's Arthexis capability contracts; the
extension owns any migrations or fixtures needed to create its Suite Feature
records.

An installed extension repository is added to `sys.path` before Django finalizes
`INSTALLED_APPS`. Extension apps can therefore provide normal Django models,
migrations, admin registrations, management commands, templates, and static
assets without copying code into the main Arthexis repository.

## Declaring extensions

Persistent extension selections live in `extensions/extensions.toml`:

```toml
[extensions]
printers = "arthexis/arthexis-printers"
another = "example/arthexis-another"
```

Deployments may also supply comma-, semicolon-, or whitespace-separated
repositories through `ARTHEXIS_EXTENSIONS` or `ARTHEXIS_EXTENSION_REPOS`.
Environment declarations are combined with the file declarations.

Short names use the default GitHub owner and the `arthexis-` repository prefix.
For example, `printers` resolves to `arthexis/arthexis-printers`.

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
.venv/bin/python manage.py extensions install printers another
```

Clone every missing declaration and fast-forward existing managed checkouts:

```bash
.venv/bin/python manage.py extensions sync
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
