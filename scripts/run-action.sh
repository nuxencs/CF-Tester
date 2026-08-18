#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
guides_path="."
junit_path="cf-test-results.xml"
application=""
channel=""
worker_release=""
expected_manifest_sha256=""

require_option_value() {
  if [[ $# -lt 2 ]]; then
    echo "ERROR: $1 requires a value" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --application)
      require_option_value "$@"
      application="$2"
      shift 2
      ;;
    --channel)
      require_option_value "$@"
      channel="$2"
      shift 2
      ;;
    --guides-path)
      require_option_value "$@"
      guides_path="$2"
      shift 2
      ;;
    --junit)
      require_option_value "$@"
      junit_path="$2"
      shift 2
      ;;
    --worker-release)
      require_option_value "$@"
      worker_release="$2"
      shift 2
      ;;
    --manifest-sha256)
      require_option_value "$@"
      expected_manifest_sha256="$2"
      shift 2
      ;;
    *)
      echo "ERROR: unknown argument '$1'" >&2
      exit 2
      ;;
  esac
done

if [[ "$junit_path" == *$'\n'* || "$junit_path" == *$'\r'* ]]; then
  echo "ERROR: junit path must not contain a line break" >&2
  exit 2
fi

if ! python3 "$tester_dir/scripts/channel_matrix.py" lock-target \
  "$tester_dir/channels.lock.json" "$application" "$channel" >/dev/null; then
  exit 2
fi

case "$application" in
  radarr)
    worker_name="RadarrWorker"
    formats="$guides_path/docs/json/radarr/cf"
    profiles="$guides_path/docs/json/radarr/quality-profiles"
    cases="$guides_path/tests/custom-formats/radarr/cases.json"
    ;;
  sonarr)
    worker_name="SonarrWorker"
    formats="$guides_path/docs/json/sonarr/cf"
    profiles="$guides_path/docs/json/sonarr/quality-profiles"
    cases="$guides_path/tests/custom-formats/sonarr/cases.json"
    ;;
esac
if [[ ! -d "$formats" ]] || [[ ! -d "$profiles" ]] || [[ ! -f "$cases" ]]; then
  echo "ERROR: '$guides_path' does not contain the $application Custom Format tests" >&2
  exit 2
fi

worker_dir="${CF_TESTER_WORKER_DIR:-}"
if [[ -z "$worker_dir" ]]; then
  if [[ ! "$worker_release" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "ERROR: worker-release must be a release tag" >&2
    exit 2
  fi
  if [[ ! "$expected_manifest_sha256" =~ ^[a-f0-9]{64}$ ]]; then
    echo "ERROR: manifest-sha256 must be a lowercase SHA-256 value" >&2
    exit 2
  fi

  download_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/cf-tester.XXXXXX")"
  release_url="${CF_TESTER_RELEASE_URL:-}"
  if [[ -z "$release_url" ]]; then
    repository="${CF_TESTER_REPOSITORY:-}"
    if [[ ! "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
      echo "ERROR: action repository must use the owner/repository format" >&2
      exit 2
    fi
    release_url="https://github.com/$repository/releases/download/$worker_release"
  fi
  manifest="$download_dir/cf-tester-manifest.json"

  curl --fail --location --retry 3 --silent --show-error \
    "$release_url/cf-tester-manifest.json" --output "$manifest"
  if command -v sha256sum >/dev/null; then
    actual_manifest_sha256="$(sha256sum "$manifest" | awk '{print $1}')"
  else
    actual_manifest_sha256="$(shasum -a 256 "$manifest" | awk '{print $1}')"
  fi
  if [[ "$actual_manifest_sha256" != "$expected_manifest_sha256" ]]; then
    echo "ERROR: checksum verification failed for cf-tester-manifest.json" >&2
    exit 2
  fi

  target_values="$(python3 "$tester_dir/scripts/channel_matrix.py" manifest-target \
    "$manifest" "$application" "$channel" --release "$worker_release" \
    --format tsv)" || exit 2
  IFS=$'\t' read -r archive expected_archive_sha256 _ _ _ <<<"$target_values"
  curl --fail --location --retry 3 --silent --show-error \
    "$release_url/$archive" --output "$download_dir/$archive"
  if command -v sha256sum >/dev/null; then
    actual_archive_sha256="$(sha256sum "$download_dir/$archive" | awk '{print $1}')"
  else
    actual_archive_sha256="$(shasum -a 256 "$download_dir/$archive" | awk '{print $1}')"
  fi
  if [[ "$actual_archive_sha256" != "$expected_archive_sha256" ]]; then
    echo "ERROR: checksum verification failed for $archive" >&2
    exit 2
  fi

  worker_dir="$download_dir/worker"
  mkdir -p "$worker_dir"
  tar -xzf "$download_dir/$archive" -C "$worker_dir"
  manifest_argument=(--manifest "$manifest")
else
  worker_dir="$worker_dir/$application/$channel"
  manifest_argument=()
fi

worker="$worker_dir/$worker_name"
if [[ ! -x "$worker" ]]; then
  echo "ERROR: worker artifact does not contain an executable $application worker" >&2
  exit 2
fi
target="$worker_dir/target.json"
if [[ ! -f "$target" ]]; then
  echo "ERROR: worker artifact does not contain target.json" >&2
  exit 2
fi
target_values="$(python3 "$tester_dir/scripts/channel_matrix.py" validate-artifact \
  "$target" "$application" "$channel" "${manifest_argument[@]}" --format tsv)" || exit 2
IFS=$'\t' read -r _ _ _ upstream_version upstream_commit <<<"$target_values"

mkdir -p "$(dirname "$junit_path")"
results_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/cf-tester-results.XXXXXX")"
results="$results_dir/$application.json"

set +e
"$worker" "$formats" "$profiles" "$cases" >"$results"
worker_status=$?
set -e

if [[ $worker_status -eq 2 ]]; then
  echo "ERROR: a worker rejected its input or setup" >&2
  exit 2
fi
if [[ $worker_status -gt 2 ]]; then
  echo "ERROR: a worker stopped unexpectedly" >&2
  exit 2
fi

set +e
python3 "$tester_dir/scripts/report_results.py" \
  --results "$results" \
  --junit "$junit_path" \
  --target "$target"
report_status=$?
set -e

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "junit=$junit_path"
    echo "upstream-version=$upstream_version"
    echo "upstream-commit=$upstream_commit"
  } >>"$GITHUB_OUTPUT"
fi

exit "$report_status"
