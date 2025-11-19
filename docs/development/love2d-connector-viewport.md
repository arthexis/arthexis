# Love2D connector viewport

This integration adds a small Love2D scene that mirrors connector status from the Django database. The management command prepares a runnable Love2D project, writes connector data to `data/connectors.json`, and can optionally launch the desktop window so the car-charging widgets are visible immediately.

## Running the viewport

1. Generate the project assets and connector snapshot:

   ```bash
   python manage.py love2d_viewport --output-dir /tmp/love2d_viewport
   ```

2. Launch the window (requires the `love` binary on your PATH):

   ```bash
   love /tmp/love2d_viewport
   ```

3. Press `R` inside the Love2D window to reload `data/connectors.json` if you regenerate the snapshot while the viewport is open.

Flags:

- `--skip-launch` writes the project and snapshot without starting Love2D (handy for CI or remote servers).
- `--love-binary` lets you point to a custom Love2D executable.
- `--output-dir` controls where the game files and data snapshot are staged. When omitted, the command uses a temporary directory and cleans it up after the Love2D process exits.

## Widget layout

Each connector with a non-empty `connector_id` appears as a card that shows:

- The display name (or serial number) and connector label
- The OCPP status label in the status color defined by `STATUS_BADGE_MAP`
- A simple car outline and battery bar; connectors marked as charging pulse the battery fill to visualize activity
- A reload hint when no connectors are available

The Love2D assets live under `ocpp/love2d_viewport/` and rely on `love.data.decode("json")` to parse the snapshot emitted by the management command.
