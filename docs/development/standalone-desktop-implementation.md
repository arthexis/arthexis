# Standalone Desktop Implementation Plan

This document proposes a concrete path to ship Arthexis as a standalone desktop app that uses the existing website as its UI while preserving the current Django architecture.

## Goals

- Provide a single executable launch experience for non-technical users.
- Keep Django as the backend and web UI source of truth.
- Support offline/local-first startup on `localhost`.
- Keep Linux and Windows parity for MVP.

## Recommended Stack

- **Desktop shell:** Tauri (Rust host + native webview)
- **Backend process:** dedicated Python launcher module (`arthexis.desktop_launcher`)
- **IPC contract:** local HTTP only (`127.0.0.1:<port>`)
- **Packaging:**
  - Windows: NSIS/MSI from Tauri bundle
  - Linux: AppImage + `.deb`

### Why Tauri

- Small runtime footprint compared with Electron.
- Native installers and update flow support.
- Predictable process management for starting/stopping a child backend.
- Uses system webview so the current web UI can be reused with minimal change.

## Delivery Scope

### MVP (Milestone 1)

1. New desktop launcher executable starts backend if not already running.
2. Launcher waits for health endpoint success and opens app window.
3. Launcher shuts down child backend on app exit.
4. Single-click install artifacts for Windows and Linux.

### Post-MVP (Milestone 2)

1. Auto-update channel integration.
2. Crash recovery and backend restart policy.
3. Optional admin/public route picker at startup.
4. Signed binaries and release provenance attestations.

## Concrete Implementation Steps

## 1) Create desktop workspace

Add a new top-level folder:

- `desktop/tauri/`
  - `src-tauri/` (Rust host)
  - `src/` (minimal front-end shell that loads localhost URL)

The shell should not duplicate Arthexis pages; it only hosts a webview pointing at localhost.

## 2) Define backend lifecycle contract

Add a dedicated backend health endpoint for launcher readiness checks:

- `GET /healthz/` returns:
  - `200` and JSON payload with app version, role, and migration readiness.

Launcher sequence:

1. Pick preferred port (default `8888`, fallback to available port).
2. Spawn backend command with explicit env (`ARTHEXIS_PORT`, `DJANGO_SETTINGS_MODULE`, optional role flags).
3. Poll `/healthz/` with timeout (for example 60s).
4. Open webview to `127.0.0.1:<port>/` over HTTP.
5. On close: send termination signal and wait graceful timeout before forced stop.

## 3) Add a stable Python launcher module

Create a package module as a stable backend process target:

- `arthexis/desktop_launcher.py`

Responsibilities:

- Parse startup flags (`--port`, `--role`, `--no-migrate`).
- Run preflight checks (migrations/checks) equivalent to current start scripts.
- Start Django ASGI/WSGI server in a non-dev mode suitable for desktop use.

Use this module as the canonical command Tauri starts, instead of invoking shell scripts directly.

## 4) Configuration and security defaults

Desktop mode defaults:

- Bind to `127.0.0.1` (not `0.0.0.0`) unless explicitly overridden.
- Restrict allowed hosts to localhost variants.
- Disable debug features by default in packaged desktop builds.
- Add a desktop profile flag, e.g. `ARTHEXIS_DESKTOP_MODE=1`, to gate desktop-specific behavior.

## 5) Packaging and CI/CD

Add CI jobs:

1. Build backend wheel/sdist.
2. Build Tauri app for Windows and Linux.
3. Attach installers as workflow artifacts.
4. (Later) sign artifacts and publish to release channels.

Proposed workflow files:

- `.github/workflows/desktop-build.yml`
- `.github/workflows/desktop-release.yml`

## 6) Telemetry and logs

For supportability, store launcher logs separately from Django logs:

- Windows: `%LOCALAPPDATA%/Arthexis/logs/launcher.log`
- Linux: `~/.local/share/arthexis/logs/launcher.log`

Also keep backend stdout/stderr capture in rotating files managed by launcher.

## 7) Incremental rollout plan

- **Internal — Phase A:** unsigned dev builds, internal testers only.
- **Beta — Phase B:** signed installers, upgrade path tested.
- **General availability — Phase C:** release through standard channel, docs and support runbooks updated.

## Acceptance Criteria

- Double-clicking installer and launching app shows Arthexis UI without opening an external browser.
- App starts from cold boot in under 20 seconds on reference hardware.
- App exit terminates child backend cleanly with no orphan process.
- Re-launch after unclean crash recovers automatically.
- Existing web workflows (login, admin, core pages) work unchanged.

## Risks and Mitigations

- **Risk:** Port conflicts.
  - **Mitigation:** deterministic fallback port scan and in-app error diagnostics.
- **Risk:** Migration delay at startup.
  - **Mitigation:** splash screen with progress and timeout diagnostics.
- **Risk:** Webview engine differences across OS.
  - **Mitigation:** compatibility smoke tests on both Windows and Linux targets.

## Immediate Next Tasks (Sprint-ready)

1. Add `/healthz/` endpoint and test coverage.
2. Add `arthexis.desktop_launcher` Python module.
3. Scaffold Tauri workspace under `desktop/tauri`.
4. Implement process spawn + readiness polling + graceful shutdown.
5. Add desktop build workflow with unsigned artifacts.
