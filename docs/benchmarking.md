# Benchmarking install, start, and upgrade

The helper script at `scripts/benchmark-suite.sh` runs the full install, start, and upgrade workflows while recording elapsed time for each stage.

## Usage

```bash
# Default run (installs without starting services, then starts and upgrades)
./scripts/benchmark-suite.sh

# Skip particular stages or override arguments
./scripts/benchmark-suite.sh \
  --skip-upgrade \
  --install-args "--no-start --clean --terminal" \
  --start-args "--silent" \
  --run-id custom-benchmark
```

Each run writes a tab-separated summary to `work/suite-benchmark-<run_id>.log` and echoes a human-readable summary to the terminal. The run ID also flows through to the existing timing instrumentation in `install.sh` and `upgrade.sh` when present, making it easier to correlate detailed timings across files.
