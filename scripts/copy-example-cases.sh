#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
guides_dir="${1:-}"

if [[ -z "$guides_dir" ]] || [[ ! -d "$guides_dir/docs/json" ]]; then
  echo "usage: copy-example-cases.sh <guides-checkout>" >&2
  exit 2
fi

for application in radarr sonarr; do
  destination="$guides_dir/tests/custom-formats/$application"
  mkdir -p "$destination"
  cp "$tester_dir/examples/$application.json" "$destination/cases.json"
done
