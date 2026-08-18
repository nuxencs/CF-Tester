#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
application="${1:-}"
channel="${2:-}"
upstream_source="${3:-}"
output_dir="${4:-}"

if [[ -z "$application" ]] || [[ -z "$channel" ]] || \
  [[ -z "$upstream_source" ]] || [[ -z "$output_dir" ]]; then
  echo "usage: build-release.sh <application> <channel> <upstream-source> <empty-output-directory>" >&2
  exit 2
fi

mkdir -p "$output_dir"
target_values="$(python3 "$tester_dir/scripts/channel_matrix.py" lock-target \
  "$tester_dir/channels.lock.json" "$application" "$channel" --format tsv)" || exit 2
IFS=$'\t' read -r _ _ _ version commit <<<"$target_values"
binary_archive="cf-tester-$application-$channel-linux-x64.tar.gz"
source_archive="cf-tester-$application-$channel-source.tar.gz"

if [[ -e "$output_dir/$binary_archive" ]] || [[ -e "$output_dir/$source_archive" ]]; then
  echo "ERROR: release output already exists for $application/$channel" >&2
  exit 2
fi
if [[ "$(git -C "$upstream_source" rev-parse HEAD)" != "$commit" ]]; then
  echo "ERROR: $application/$channel source does not match channels.lock.json" >&2
  exit 2
fi
if [[ -n "$(git -C "$tester_dir" status --porcelain)" ]]; then
  echo "ERROR: CF-Tester source must be committed before release packaging" >&2
  exit 2
fi

staging_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/cf-tester-release.XXXXXX")"
binary_dir="$staging_dir/binary"
source_dir="$staging_dir/source"
mkdir -p "$binary_dir" "$source_dir/cf-tester" "$source_dir/upstream"
"$tester_dir/scripts/build-worker.sh" \
  "$application" "$channel" "$upstream_source" "$binary_dir"

git -C "$tester_dir" archive HEAD | tar -x -C "$source_dir/cf-tester"
git -C "$upstream_source" archive "$commit" | tar -x -C "$source_dir/upstream"

source_date_epoch="$(git -C "$tester_dir" show -s --format=%ct HEAD)"
tar_options=(
  --sort=name
  --mtime="@$source_date_epoch"
  --owner=0
  --group=0
  --numeric-owner
)
if tar --help 2>&1 | grep -q -- "--sort"; then
  tar "${tar_options[@]}" -czf "$output_dir/$binary_archive" -C "$binary_dir" .
  tar "${tar_options[@]}" -czf "$output_dir/$source_archive" -C "$source_dir" .
else
  echo "WARNING: host tar cannot create reproducible archives" >&2
  tar -czf "$output_dir/$binary_archive" -C "$binary_dir" .
  tar -czf "$output_dir/$source_archive" -C "$source_dir" .
fi

cat <<EOF
Built CF-Tester $application/$channel release artifacts.
Upstream: $version ($commit)
Output: $output_dir
EOF
