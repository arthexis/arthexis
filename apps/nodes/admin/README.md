# Nodes admin package

[![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/)

The Django admin setup for the nodes app is split across focused modules to keep the large surface area manageable:

[View all Developer Documents](../../../docs/index.md)

> Note: this link targets the in-repo docs index for repository readers, not a runtime web route.

For release confidence criteria and maturity semantics, see the [Versioning and Maturity Policy](https://github.com/arthexis/arthexis/blob/main/docs/development/versioning-maturity-policy.md).

For a module-by-module map, see the dedicated reference:

- [`docs/development/nodes-admin-package-reference.md`](../../../docs/development/nodes-admin-package-reference.md)

Import `apps.nodes.admin` (the package) to ensure all admin registrations are evaluated; `__init__.py` re-exports the registered admin classes for convenience.
