# Docker role presets

The container entrypoint supports role presets through `ARTHEXIS_ROLE_PRESET`.

- Supported values: `terminal` (default), `control`, `satellite`, `watchtower` (alias: `constellation`, for backward compatibility/convenience).
- `NODE_ROLE` still has priority. If `NODE_ROLE` is set explicitly, it overrides `ARTHEXIS_ROLE_PRESET`.
- Optional feature toggles (`ENABLE_CELERY`, `ENABLE_LCD_SCREEN`, `ENABLE_RFID_SERVICE`, `ENABLE_CAMERA_SERVICE`, and `ENABLE_CONTROL`) can be set directly to override preset defaults.
- Preset toggle defaults are derived from the effective role after applying any explicit `NODE_ROLE` override.

## Examples

Run a Control node:

```bash
docker run --rm -p 8888:8888 \
  -e ARTHEXIS_ROLE_PRESET=control \
  arthexis:latest
```

Run a Satellite node:

```bash
docker run --rm -p 8888:8888 \
  -e ARTHEXIS_ROLE_PRESET=satellite \
  arthexis:latest
```

Override the role explicitly with `NODE_ROLE`:

```bash
docker run --rm -p 8888:8888 \
  -e ARTHEXIS_ROLE_PRESET=satellite \
  -e NODE_ROLE=Control \
  arthexis:latest
```
