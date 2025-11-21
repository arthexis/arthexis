# Pyxel connector viewport

This integration uses [Pyxel](https://github.com/kitao/pyxel) to mirror connector status from the Django database. The management command prepares a runnable Pyxel project, writes connector data to `data/connectors.json`, and can optionally launch the desktop window so the car-charging widgets are visible immediately.

## Running the viewport

1. Generate the project assets and connector snapshot:

   ```bash
   python manage.py pyxel_viewport --output-dir /tmp/pyxel_viewport
   ```

2. Launch the window (requires the `pyxel` CLI on your PATH):

   ```bash
   cd /tmp/pyxel_viewport
   pyxel run main.py
   ```

3. Press `R` inside the Pyxel window to reload `data/connectors.json` if you regenerate the snapshot while the viewport is open.

Flags:

- `--skip-launch` writes the project and snapshot without starting Pyxel (handy for CI or remote servers).
- `--pyxel-runner` lets you point to a custom Pyxel executable (for example `python -m pyxel`).
- `--output-dir` controls where the game files and data snapshot are staged. When omitted, the command uses a temporary directory and cleans it up after the Pyxel process exits.

Helper scripts simplify common invocations:

- Unix-like environments: `./pyxel-viewport.sh` creates (or refreshes) a clean work directory under `work/pyxel_viewport` and launches the viewport with the `pyxel` CLI by default.
- Windows: `pyxel-viewport.bat` mirrors the same behavior, honoring the `WORK_DIR` and `PYXEL_RUNNER` environment variables or `--work-dir` / `--pyxel-runner` arguments to override defaults.

## Widget layout

Each connector with a non-empty `connector_id` appears as a card that shows:

- The display name (or serial number) and connector label
- The OCPP status label in a palette-matched status color
- A simple car outline and battery bar; connectors marked as charging pulse the battery fill to visualize activity
- A reload hint when no connectors are available

The Pyxel assets live under `ocpp/pyxel_viewport/` and rely on Python's built-in JSON parser to load the snapshot emitted by the management command.
