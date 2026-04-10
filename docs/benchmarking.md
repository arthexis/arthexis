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

## OCPP memory-profile benchmark

Use `benchmark_ocpp_memory` to compare OCPP feature-operation performance against maximum device memory profiles.

```bash
# Human-readable summary for the default 1/2/3/4 GiB profiles
.venv/bin/python manage.py benchmark_ocpp_memory

# JSON output for automation pipelines
.venv/bin/python manage.py benchmark_ocpp_memory \
  --memory-gb 1 2 3 4 \
  --versions 1.6J 2.0.1 2.1 \
  --json
```

The command runs OCPP coverage operations for each selected version/profile pair, records elapsed runtime and peak RSS, and reports memory utilization as a percentage of each device-memory profile. This models devices where the listed memory value is total system RAM (not dedicated to Arthexis).
