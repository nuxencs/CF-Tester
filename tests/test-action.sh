#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
guides_dir="${GUIDES_DIR:-}"
if [[ -z "$guides_dir" ]]; then
  echo "ERROR: set GUIDES_DIR to a Guides checkout" >&2
  exit 2
fi
guides_dir="$(cd "$guides_dir" && pwd)"
temp_dir="$(mktemp -d)"
junit_path="$temp_dir/radarr-results.xml"
action_output="$temp_dir/action-output"
example_guides_dir="$temp_dir/Guides"
mkdir -p "$example_guides_dir"
ln -s "$guides_dir/docs" "$example_guides_dir/docs"
"$tester_dir/scripts/copy-example-cases.sh" "$example_guides_dir"
guides_dir="$example_guides_dir"

python3 - "$tester_dir/action.yml" <<'PY'
import sys
from pathlib import Path

action = Path(sys.argv[1]).read_text(encoding="utf-8")
run_block = action.split("      run: >-", maxsplit=1)[1]
run_block = run_block.split("\n\nbranding:", maxsplit=1)[0]
assert "${{" not in run_block
for name in (
    "application",
    "channel",
    "guides-path",
    "worker-release",
    "manifest-sha256",
    "junit-path",
):
    assert f"${{{{ inputs.{name} }}}}" in action
assert "CF_TESTER_REPOSITORY: ${{ github.action_repository }}" in action
PY

echo "Action input boundary test passed."

missing_repository_error="$temp_dir/missing-repository-error"
status=0
"$tester_dir/scripts/run-action.sh" \
  --application radarr \
  --channel stable \
  --guides-path "$guides_dir" \
  --worker-release v-test \
  --manifest-sha256 "$(printf '0%.0s' {1..64})" \
  --junit "$temp_dir/missing-repository-results.xml" \
  >"$temp_dir/missing-repository-output" \
  2>"$missing_repository_error" || status=$?
[[ $status -eq 2 ]] || {
  echo "FAIL: missing action repository returned $status instead of 2" >&2
  exit 1
}
grep -Fx "ERROR: action repository must use the owner/repository format" \
  "$missing_repository_error" >/dev/null

echo "Missing action repository test passed."

missing_value_error="$temp_dir/missing-value-error"
status=0
"$tester_dir/scripts/run-action.sh" --application \
  >"$temp_dir/missing-value-output" 2>"$missing_value_error" || status=$?
[[ $status -eq 2 ]] || {
  echo "FAIL: missing option value returned $status instead of 2" >&2
  exit 1
}
grep -Fx "ERROR: --application requires a value" "$missing_value_error" >/dev/null

echo "Missing option value test passed."

status=0
"$tester_dir/scripts/run-action.sh" \
  --application radarr \
  --channel stable \
  --guides-path "$guides_dir" \
  --junit $'results.xml\ninjected=value' \
  >"$temp_dir/newline-output" 2>"$temp_dir/newline-error" || status=$?
[[ $status -eq 2 ]] || {
  echo "FAIL: JUnit path with a line break returned $status instead of 2" >&2
  exit 1
}
grep -Fx "ERROR: junit path must not contain a line break" \
  "$temp_dir/newline-error" >/dev/null

echo "JUnit output injection test passed."

GITHUB_OUTPUT="$action_output" \
  CF_TESTER_WORKER_DIR="$tester_dir/tests/fixtures/action-workers" \
  "$tester_dir/scripts/run-action.sh" \
  --application radarr \
  --channel stable \
  --guides-path "$guides_dir" \
  --junit "$junit_path"

python3 - "$junit_path" <<'PY'
import sys
import xml.etree.ElementTree as ET

suite = ET.parse(sys.argv[1]).getroot()
assert suite.attrib["name"] == "CF-Tester radarr/stable"
assert suite.attrib["tests"] == "1"
assert suite.attrib["failures"] == "0"
properties = {
    item.attrib["name"]: item.attrib["value"]
    for item in suite.findall("./properties/property")
}
assert properties["application"] == "radarr"
assert properties["channel"] == "stable"
PY
grep -Fx "junit=$junit_path" "$action_output" >/dev/null
grep -Fx "upstream-version=test-radarr" "$action_output" >/dev/null
grep -Fx "upstream-commit=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  "$action_output" >/dev/null

echo "Action wrapper test passed."

release_dir="$temp_dir/release/v-test"
mkdir -p "$release_dir"
matrix="$(python3 "$tester_dir/scripts/channel_matrix.py" lock-matrix "$tester_dir/channels.lock.json")"
for target in $(jq -r '.include[] | "\(.application)/\(.channel)"' <<<"$matrix"); do
  application="${target%/*}"
  channel="${target#*/}"
  package_dir="$temp_dir/package-$application-$channel"
  mkdir -p "$package_dir"
  if [[ "$application" == "radarr" ]]; then
    cp "$tester_dir/tests/fixtures/action-workers/radarr/stable/RadarrWorker" \
      "$package_dir/RadarrWorker"
  else
    cp "$tester_dir/tests/fixtures/action-workers/sonarr/stable/SonarrWorker" \
      "$package_dir/SonarrWorker"
  fi
  python3 "$tester_dir/scripts/channel_matrix.py" lock-target \
    "$tester_dir/channels.lock.json" "$application" "$channel" \
    >"$package_dir/target.json"
  tar -czf "$release_dir/cf-tester-$application-$channel-linux-x64.tar.gz" \
    -C "$package_dir" .
  tar -czf "$release_dir/cf-tester-$application-$channel-source.tar.gz" \
    -C "$package_dir" target.json
done
python3 "$tester_dir/scripts/channel_matrix.py" build-manifest \
  "$tester_dir/channels.lock.json" v-test "$release_dir" \
  "$release_dir/cf-tester-manifest.json"
manifest_sha256="$(shasum -a 256 "$release_dir/cf-tester-manifest.json" | awk '{print $1}')"

CF_TESTER_RELEASE_URL="file://$release_dir" \
  "$tester_dir/scripts/run-action.sh" \
  --application sonarr \
  --channel stable \
  --guides-path "$guides_dir" \
  --worker-release v-test \
  --manifest-sha256 "$manifest_sha256" \
  --junit "$temp_dir/download-results.xml"

echo "Action download test passed."

printf 'changed' >>"$release_dir/cf-tester-radarr-stable-linux-x64.tar.gz"
status=0
CF_TESTER_RELEASE_URL="file://$release_dir" \
  "$tester_dir/scripts/run-action.sh" \
  --application radarr \
  --channel stable \
  --guides-path "$guides_dir" \
  --worker-release v-test \
  --manifest-sha256 "$manifest_sha256" \
  --junit "$temp_dir/rejected-artifact-results.xml" >/dev/null 2>&1 || status=$?
[[ $status -eq 2 ]] || {
  echo "FAIL: changed worker archive returned $status instead of 2" >&2
  exit 1
}

echo "Action worker checksum rejection test passed."

status=0
CF_TESTER_RELEASE_URL="file://$release_dir" \
  "$tester_dir/scripts/run-action.sh" \
  --application radarr \
  --channel stable \
  --guides-path "$guides_dir" \
  --worker-release v-test \
  --manifest-sha256 "$(printf '0%.0s' {1..64})" \
  --junit "$temp_dir/rejected-results.xml" >/dev/null 2>&1 || status=$?
[[ $status -eq 2 ]] || {
  echo "FAIL: wrong manifest checksum returned $status instead of 2" >&2
  exit 1
}

echo "Action manifest checksum rejection test passed."
