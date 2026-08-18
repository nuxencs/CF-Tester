#!/usr/bin/env bash
set -euo pipefail

tester_dir="$(cd "$(dirname "$0")/.." && pwd)"
guides_dir="${1:-}"

if [[ -z "$guides_dir" ]]; then
  echo "usage: check-repository.sh <guides-checkout>" >&2
  exit 2
fi

bash -n "$tester_dir"/scripts/*.sh "$tester_dir"/tests/*.sh
shellcheck "$tester_dir"/scripts/*.sh "$tester_dir"/tests/*.sh
python3 -m unittest discover -s "$tester_dir/tests" -p 'test_*.py'
GUIDES_DIR="$guides_dir" "$tester_dir/tests/test-action.sh"
