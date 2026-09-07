# GWAY

Arthexis exposes its Django management-command surface through GWAY using the repository `gway.toml` manifest.

For development against a local checkout:

```bash
python -m pip install "git+https://github.com/arthexis/gway.git@main"
gway register .
gway arthexis --help
gway arthexis check
gway arthexis migrate --plan
```

For a managed installation on a clean machine:

```bash
gway install arthexis
gway arthexis check
gway arthexis migrate --plan
```

GWAY owns project discovery, generated project help, argument dispatch, and final command routing. Arthexis remains responsible for its Django settings, dependencies, management commands, and application-specific behavior.

The Django adapter enters Arthexis through `config.settings` and Django's native management-command registry. Do not add Arthexis-specific command definitions to GWAY core.

System/appliance installations should use the same `gway arthexis ...` command surface once GWAY's appliance installation mode is available; until then, existing Arthexis provisioning remains authoritative for deployment layout and service management.
