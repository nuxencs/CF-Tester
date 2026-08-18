#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
guides_dir="${GUIDES_DIR:-}"
application="${1:-}"
channel="${2:-}"
target_file="${3:-}"

if [[ -z "$application" ]] || [[ -z "$channel" ]] || [[ -z "$guides_dir" ]]; then
  echo "usage: test-from-source.sh <application> <channel> [target-file]" >&2
  echo "set GUIDES_DIR to a Guides checkout" >&2
  exit 2
fi

case "$application" in
  radarr)
    upstream_source="${RADARR_SOURCE:-}"
    source_variable="RADARR_SOURCE"
    dotnet_command="${RADARR_DOTNET:-dotnet}"
    project="$tester_dir/src/RadarrWorker/RadarrWorker.csproj"
    source_property="RadarrSource"
    nuget_config="$upstream_source/src/NuGet.config"
    worker="$tester_dir/src/RadarrWorker/bin/Debug/net8.0/RadarrWorker.dll"
    formats="$guides_dir/docs/json/radarr/cf"
    profiles="$guides_dir/docs/json/radarr/quality-profiles"
    cases="$guides_dir/tests/custom-formats/radarr/cases.json"
    ;;
  sonarr)
    upstream_source="${SONARR_SOURCE:-}"
    source_variable="SONARR_SOURCE"
    dotnet_command="${SONARR_DOTNET:-dotnet}"
    project="$tester_dir/src/SonarrWorker/SonarrWorker.csproj"
    source_property="SonarrSource"
    nuget_config="$upstream_source/src/NuGet.Config"
    worker="$tester_dir/src/SonarrWorker/bin/Debug/net6.0/SonarrWorker.dll"
    formats="$guides_dir/docs/json/sonarr/cf"
    profiles="$guides_dir/docs/json/sonarr/quality-profiles"
    cases="$guides_dir/tests/custom-formats/sonarr/cases.json"
    ;;
  *)
    echo "ERROR: application must be radarr or sonarr" >&2
    exit 2
    ;;
esac

if [[ -z "$upstream_source" ]]; then
  echo "ERROR: set $source_variable to an application checkout" >&2
  exit 2
fi
if ! git -C "$guides_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: GUIDES_DIR is not a Git checkout: $guides_dir" >&2
  exit 2
fi
if ! git -C "$upstream_source" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: application source is not a Git checkout: $upstream_source" >&2
  exit 2
fi

guides_dir="$(git -C "$guides_dir" rev-parse --show-toplevel)"
upstream_source="$(git -C "$upstream_source" rev-parse --show-toplevel)"
nuget_config="$upstream_source/src/$(basename "$nuget_config")"
project_dir="$(dirname "$project")"
project_file="$(basename "$project")"

if [[ -n "$target_file" ]]; then
  target_values="$(python3 "$tester_dir/scripts/channel_matrix.py" \
    validate-artifact "$target_file" "$application" "$channel" \
    --format tsv)" || exit 2
else
  target_values="$(python3 "$tester_dir/scripts/channel_matrix.py" lock-target \
    "$tester_dir/channels.lock.json" "$application" "$channel" \
    --format tsv)" || exit 2
fi
IFS=$'\t' read -r _ _ _ _ commit <<<"$target_values"

if [[ "$(git -C "$upstream_source" rev-parse HEAD)" != "$commit" ]]; then
  echo "ERROR: $application/$channel source does not match the selected target" >&2
  exit 2
fi

results_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/cf-tester-source-results.XXXXXX")"

(
  cd "$project_dir"
  "$dotnet_command" restore "$project_file" \
    -p:"$source_property"="$upstream_source" \
    -p:EnableAnalyzers=false \
    -p:SentryUploadSymbols=false \
    --configfile "$nuget_config"
  "$dotnet_command" build "$project_file" --no-restore \
    -p:"$source_property"="$upstream_source" \
    -p:EnableAnalyzers=false \
    -p:SentryUploadSymbols=false
)

GUIDES_DIR="$guides_dir" \
  RADARR_DOTNET="${RADARR_DOTNET:-dotnet}" \
  SONARR_DOTNET="${SONARR_DOTNET:-dotnet}" \
  "$tester_dir/tests/test-cli.sh" "$application"

if [[ -n "$target_file" ]]; then
  cp "$target_file" "$results_dir/target.json"
else
  python3 "$tester_dir/scripts/channel_matrix.py" lock-target \
    "$tester_dir/channels.lock.json" "$application" "$channel" \
    >"$results_dir/target.json"
fi

set +e
"$dotnet_command" "$worker" "$formats" "$profiles" "$cases" >"$results_dir/results.json"
worker_status=$?
set -e

if [[ $worker_status -eq 2 ]]; then
  echo "ERROR: worker rejected its input or setup" >&2
  exit 2
fi
if [[ $worker_status -gt 2 ]]; then
  echo "ERROR: worker stopped unexpectedly" >&2
  exit 2
fi

python3 "$tester_dir/scripts/report_results.py" \
  --results "$results_dir/results.json" \
  --junit "$results_dir/cf-test-results.xml" \
  --target "$results_dir/target.json"
