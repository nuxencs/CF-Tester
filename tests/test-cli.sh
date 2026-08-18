#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
guides_dir="${GUIDES_DIR:-}"
radarr_worker="$tester_dir/src/RadarrWorker/bin/Debug/net8.0/RadarrWorker.dll"
sonarr_worker="$tester_dir/src/SonarrWorker/bin/Debug/net6.0/SonarrWorker.dll"
radarr_dotnet="${RADARR_DOTNET:-dotnet}"
sonarr_dotnet="${SONARR_DOTNET:-dotnet}"
application="${1:-all}"

if [[ -z "$guides_dir" ]]; then
  echo "ERROR: set GUIDES_DIR to a Guides checkout" >&2
  exit 2
fi
if [[ "$application" != "all" ]] && [[ "$application" != "radarr" ]] && \
  [[ "$application" != "sonarr" ]]; then
  echo "usage: test-cli.sh [radarr|sonarr]" >&2
  exit 2
fi

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

expect_contract_error() {
  local expected="$1"
  shift
  local output
  local status=0

  output="$("$@" 2>&1)" || status=$?
  [[ "$status" -eq 2 ]] || fail "contract error returned $status instead of 2"
  [[ "$output" == *"$expected"* ]] || fail "contract error did not contain: $expected"
  if [[ "$output" == *"Unhandled exception"* ]]; then
    fail "contract error included a stack trace"
  fi
}

if [[ "$application" == "all" ]] || [[ "$application" == "radarr" ]]; then
  expect_contract_error \
    "Unknown Radarr language 'NotALanguage'" \
    "$radarr_dotnet" "$radarr_worker" \
    "$guides_dir/docs/json/radarr/cf" \
    "$guides_dir/docs/json/radarr/quality-profiles" \
    "$tester_dir/tests/fixtures/radarr-invalid-language.json"

  status=0
  result="$($radarr_dotnet "$radarr_worker" \
    "$guides_dir/docs/json/radarr/cf" \
    "$guides_dir/docs/json/radarr/quality-profiles" \
    "$tester_dir/tests/fixtures/radarr-failed-assertion.json")" || status=$?

  [[ "$status" -eq 1 ]] || fail "failed assertion returned $status instead of 1"
  jq -e '
    .passed == false and
    .diagnostics[0].name == "FreeLeech" and
    .diagnostics[0].specifications[0].name == "FreeLeech" and
    .diagnostics[0].specifications[0].matched == false
  ' <<<"$result" >/dev/null || \
    fail "failed assertion did not include specification diagnostics"

  status=0
  result="$($radarr_dotnet "$radarr_worker" \
    "$guides_dir/docs/json/radarr/cf" \
    "$guides_dir/docs/json/radarr/quality-profiles" \
    "$tester_dir/tests/fixtures/radarr-profile-score.json")" || status=$?

  [[ "$status" -eq 0 ]] || fail "profile score case returned $status"
  jq -e '
    .passed == true and
    .profileScore.expected == 2351 and
    .profileScore.actual == 2351 and
    (.profileScore.formats | map({trashId, name, score})) == [
      {
        "trashId": "493b6d1dbec3c3364c59d7607f7e3405",
        "name": "HDR",
        "score": 500
      },
      {
        "trashId": "c20f169ef63c5f40c2def54abaf4438e",
        "name": "WEB Tier 01",
        "score": 1700
      },
      {
        "trashId": "fb392fb0d61a010ae38e49ceaa24a1ef",
        "name": "2160p",
        "score": 151
      }
    ]
  ' <<<"$result" >/dev/null || fail "profile score result was incomplete"
fi

if [[ "$application" == "all" ]] || [[ "$application" == "sonarr" ]]; then
  expect_contract_error \
    "application must be 'sonarr', not 'radarr'" \
    "$sonarr_dotnet" "$sonarr_worker" \
    "$guides_dir/docs/json/sonarr/cf" \
    "$guides_dir/docs/json/sonarr/quality-profiles" \
    "$tester_dir/tests/fixtures/sonarr-wrong-application.json"

  status=0
  result="$("$sonarr_dotnet" "$sonarr_worker" \
    "$guides_dir/docs/json/sonarr/cf" \
    "$guides_dir/docs/json/sonarr/quality-profiles" \
    "$tester_dir/tests/fixtures/sonarr-failed-assertion.json")" || status=$?

  [[ "$status" -eq 1 ]] || fail "Sonarr failed assertion returned $status instead of 1"
  jq -e '
    .passed == false and
    .diagnostics[0].name == "FreeLeech" and
    .diagnostics[0].specifications[0].name == "FreeLeech" and
    .diagnostics[0].specifications[0].matched == false
  ' <<<"$result" >/dev/null || \
    fail "Sonarr assertion did not include specification diagnostics"

  status=0
  result="$("$sonarr_dotnet" "$sonarr_worker" \
    "$guides_dir/docs/json/sonarr/cf" \
    "$guides_dir/docs/json/sonarr/quality-profiles" \
    "$tester_dir/tests/fixtures/sonarr-profile-score.json")" || status=$?

  [[ "$status" -eq 0 ]] || fail "Sonarr profile score case returned $status"
  jq -e '
    .passed == true and
    .profileScore.expected == 2200 and
    .profileScore.actual == 2200 and
    (.profileScore.formats | map({trashId, name, score})) == [
      {
        "trashId": "505d871304820ba7106b693be6fe4a9e",
        "name": "HDR",
        "score": 500
      },
      {
        "trashId": "e6258996055b9fbab7e9cb2f75819294",
        "name": "WEB Tier 01",
        "score": 1700
      }
    ]
  ' <<<"$result" >/dev/null || fail "Sonarr profile score result was incomplete"
fi

echo "CLI contract tests passed."
