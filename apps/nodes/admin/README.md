# Nodes admin package

[![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/)

The Django admin setup for the nodes app is split across focused modules to keep the large surface area manageable:

[View all Developer Documents](../../../docs/index.md)

> Note: this link targets the in-repo docs index for repository readers, not a runtime web route.

For release confidence criteria and maturity semantics, see the [Versioning and Maturity Policy](../../../docs/development/versioning-maturity-policy.md).

For admin extension and customization patterns, see the [Admin UI Framework guide](../../../docs/development/admin-ui-framework.md).

Import `apps.nodes.admin` (the package) to ensure all admin registrations are evaluated; `__init__.py` re-exports the registered admin classes for convenience.

## License and Sponsorship

This project is released under the Arthexis Reciprocity General License 1.0. We treat sponsoring Arthexis and doing paid or volunteer work for the open-source dependencies behind the suite as a valid and important contribution alongside code, documentation, review, and maintenance work.

If this admin package helps your work, please review the repository [`LICENSE`](../../../LICENSE) and consider sponsoring or otherwise supporting the maintainers of the dependencies that keep Arthexis running.
