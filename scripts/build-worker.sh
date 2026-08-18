#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
application="${1:-}"
channel="${2:-}"
upstream_source="${3:-}"
output_dir="${4:-}"
runtime="linux-x64"

if [[ -z "$application" ]] || [[ -z "$channel" ]] || \
  [[ -z "$upstream_source" ]] || [[ -z "$output_dir" ]]; then
  printf '%s\n' \
    "usage: build-worker.sh <application> <channel> <upstream-source>" \
    "       <empty-output-directory>" >&2
  exit 2
fi

target_values="$(python3 "$tester_dir/scripts/channel_matrix.py" lock-target \
  "$tester_dir/channels.lock.json" "$application" "$channel" --format tsv)" || exit 2
IFS=$'\t' read -r _ _ _ version commit <<<"$target_values"

if ! git -C "$upstream_source" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: upstream source is not a Git checkout: $upstream_source" >&2
  exit 2
fi
upstream_source="$(git -C "$upstream_source" rev-parse --show-toplevel)"
if [[ "$(git -C "$upstream_source" rev-parse HEAD)" != "$commit" ]]; then
  echo "ERROR: $application/$channel source does not match channels.lock.json" >&2
  exit 2
fi

mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
if find "$output_dir" -mindepth 1 -print -quit | grep -q .; then
  echo "ERROR: output directory must be empty: $output_dir" >&2
  exit 2
fi

case "$application" in
  radarr)
    project="$tester_dir/src/RadarrWorker/RadarrWorker.csproj"
    source_property="RadarrSource"
    nuget_config="$upstream_source/src/NuGet.config"
    dotnet_command="${RADARR_DOTNET:-dotnet}"
    ;;
  sonarr)
    project="$tester_dir/src/SonarrWorker/SonarrWorker.csproj"
    source_property="SonarrSource"
    nuget_config="$upstream_source/src/NuGet.Config"
    dotnet_command="${SONARR_DOTNET:-dotnet}"
    ;;
esac

project_dir="$(dirname "$project")"
project_file="$(basename "$project")"
(
  cd "$project_dir"
  "$dotnet_command" restore "$project_file" \
    --runtime "$runtime" \
    -p:"$source_property"="$upstream_source" \
    -p:EnableAnalyzers=false \
    -p:SentryUploadSymbols=false \
    --configfile "$nuget_config"
  "$dotnet_command" publish "$project_file" \
    --no-restore \
    --configuration Release \
    --runtime "$runtime" \
    --self-contained true \
    --output "$output_dir" \
    -p:"$source_property"="$upstream_source" \
    -p:EnableAnalyzers=false \
    -p:SentryUploadSymbols=false \
    -p:ContinuousIntegrationBuild=true
)

cp "$tester_dir/LICENSE" "$output_dir/LICENSE"
python3 "$tester_dir/scripts/channel_matrix.py" lock-target \
  "$tester_dir/channels.lock.json" "$application" "$channel" \
  >"$output_dir/target.json"

echo "Built $application/$channel worker for $version ($commit)."
