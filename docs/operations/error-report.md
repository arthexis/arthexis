# Error Report Package

`error-report.sh` builds a single diagnostic zip from the repository root without
calling Django management commands. Use it when an Arthexis node cannot start,
the virtual environment is broken, migrations fail before Django loads, or an
operator needs one package to attach to a support request.

## Usage

```bash
./error-report.sh
./error-report.sh --since 24h
./error-report.sh --output-dir work/error-reports
./error-report.sh --upload-url "https://signed-upload-url"
```

The default output path is:

```text
work/error-reports/arthexis-error-report-<hostname>-<timestamp>.zip
```

Run a no-write preview with:

```bash
./error-report.sh --dry-run
```

## Contents

The zip includes text diagnostics only:

- `manifest.json` with report metadata, options, file hashes, warnings, and
  collected entry names.
- `summary.txt` with a concise human-readable overview.
- `system/` metadata for platform, Python, and selected environment state.
- `arthexis/` metadata for version, git state, and a side-effect-free status
  snapshot.
- `arthexis/locks/` text lock files when present.
- selected recent `logs/` text files, capped by `--max-log-files` and
  `--max-file-bytes`.

`status.sh` is not invoked because it may update startup locks. The report
instead writes its own read-only status snapshot.

## Safety

The collector excludes high-risk material by default:

- environment files such as `.env` and `arthexis.env`
- databases, dumps, backups, and broad data directories
- private keys, certificate keys, and local key files
- `media/`, `static/`, caches, virtual environments, and Git internals

All copied text and command output pass through a redaction layer for common
secret-bearing values such as tokens, passwords, authorization headers, private
key blocks, AWS access keys, and credential-bearing URLs.

Secrets must not be added manually to a report before sharing it.

## Uploads

Uploading is disabled unless an explicit URL is provided:

```bash
./error-report.sh --upload-url "https://signed-upload-url"
```

The default method is `PUT`; use `--upload-method POST` only when the receiver
expects it. HTTPS is required unless `--allow-insecure-upload` is supplied for a
local or otherwise controlled endpoint.

The local zip is always created before upload. If upload fails, the command exits
with a non-zero status and leaves the zip in place.

## Useful Options

| Option | Notes |
| --- | --- |
| `--since 24h` | Include non-critical logs modified within the last 24 hours. Standard error/startup logs are still preferred. |
| `--max-log-files COUNT` | Cap the number of log files included. Defaults to `30`. |
| `--max-file-bytes BYTES` | Copy only the tail of each text file when it is larger than this limit. Defaults to `262144`. |
| `--output-dir DIR` | Write reports somewhere other than `work/error-reports`. |
| `--dry-run` | Show planned zip entries without writing a report or uploading. |
