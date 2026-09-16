#!/usr/bin/env bash
set -Eeuo pipefail

resolve_main_sha() {
  local repository="$1"
  local sha
  sha="$(git ls-remote "https://github.com/${repository}.git" refs/heads/main | awk '{print $1}')"
  if [[ -z "$sha" ]]; then
    echo "Unable to resolve ${repository}@main" >&2
    exit 1
  fi
  printf '%s\n' "$sha"
}

assert_sha() {
  local name="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "Dependency revision mismatch for ${name}: expected ${expected}, got ${actual}" >&2
    exit 1
  fi
  echo "${name}: ${actual}"
}

echo "=== resolve Watchtower dependency revisions ==="
gway_expected="$(resolve_main_sha arthexis/gway)"
web_expected="$(resolve_main_sha arthexis/gway-web)"
wire_expected="$(resolve_main_sha arthexis/gway-wire)"

printf 'gway expected: %s\n' "$gway_expected"
printf 'web expected:  %s\n' "$web_expected"
printf 'wire expected: %s\n' "$wire_expected"

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    echo "GWAY_EXPECTED_SHA=${gway_expected}"
    echo "GWAY_WEB_EXPECTED_SHA=${web_expected}"
    echo "GWAY_WIRE_EXPECTED_SHA=${wire_expected}"
  } >> "$GITHUB_ENV"
fi

echo "=== upgrade Watchtower dependencies ==="
sudo -n gway upgrade gway --force
sudo -n gway upgrade web --install
sudo -n gway upgrade wire --install

echo "=== runtime attestation context ==="
gway_executable="$(command -v gway)"
gway_venv="${GWAY_VENV:-/opt/gway/venv}"
gway_python="${gway_venv}/bin/python"
gway_runtime="${gway_venv}/bin/gway"
printf 'gway wrapper: %s\n' "$gway_executable"
printf 'gway venv: %s\n' "$gway_venv"
printf 'gway runtime: %s\n' "$gway_runtime"
printf 'gway interpreter: %s\n' "$gway_python"
printf '%s\n' '--- wrapper header ---'
sed -n '1,12p' "$gway_executable" || true
printf '%s\n' '--- managed runtime files ---'
ls -ld "$gway_venv" "$gway_python" "$gway_runtime" 2>&1 || true
if [[ ! -x "$gway_python" ]]; then
  echo "Managed GWay Python is not executable: ${gway_python}" >&2
  exit 1
fi
if [[ ! -x "$gway_runtime" ]]; then
  echo "Managed GWay runtime is not executable: ${gway_runtime}" >&2
  exit 1
fi
sudo -n "$gway_python" --version

printf '%s\n' '--- GWay package metadata ---'
sudo -n "$gway_python" - <<'PY'
import importlib.metadata
import json

try:
    dist = importlib.metadata.distribution("gway")
except importlib.metadata.PackageNotFoundError as exc:
    raise SystemExit("managed GWay interpreter cannot find the gway distribution") from exc

print(f"distribution path: {dist._path}")
raw = dist.read_text("direct_url.json")
print(f"direct_url.json present: {bool(raw)}")
if raw:
    data = json.loads(raw)
    print(f"direct URL: {data.get('url', '<missing>')}")
    vcs = data.get("vcs_info", {})
    print(f"VCS: {vcs.get('vcs', '<missing>')}")
    print(f"recorded commit: {vcs.get('commit_id', '<missing>')}")
PY

echo "=== verify installed dependency revisions ==="
gway_actual="$(sudo -n "$gway_python" - <<'PY'
import importlib.metadata
import json

raw = importlib.metadata.distribution("gway").read_text("direct_url.json")
if not raw:
    raise SystemExit("installed gway has no direct_url.json VCS metadata")
data = json.loads(raw)
try:
    print(data["vcs_info"]["commit_id"])
except KeyError as exc:
    raise SystemExit(f"installed gway lacks VCS commit metadata: {data!r}") from exc
PY
)"
assert_sha gway "$gway_expected" "$gway_actual"

web_path="$(sudo -n gway path web)"
wire_path="$(sudo -n gway path wire)"
printf 'web path: %s\n' "$web_path"
printf 'wire path: %s\n' "$wire_path"
printf '%s\n' '--- managed checkout status ---'
sudo -n git -C "$web_path" status --short --branch || true
sudo -n git -C "$wire_path" status --short --branch || true
web_actual="$(sudo -n git -C "$web_path" rev-parse HEAD)"
wire_actual="$(sudo -n git -C "$wire_path" rev-parse HEAD)"
assert_sha web "$web_expected" "$web_actual"
assert_sha wire "$wire_expected" "$wire_actual"

echo "=== dependency attestation complete ==="
gway --version
