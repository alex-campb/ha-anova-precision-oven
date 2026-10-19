#!/bin/sh
# Runs v1_compare.py on the current code and diffs against the outputs of the
# original v1.3.0 code (fixtures/v1_baseline_*). The only expected difference
# is "null" vs "{}" for fan_speed extra attributes (6 lines): both mean no
# extra attributes in Home Assistant.
# Usage: sh v1_check.sh <path to custom_components>
set -e
cd "$(dirname "$0")"
out=$(mktemp -d)
python3 v1_compare.py "$1" fixtures/v1_state.json "$out/idle.json" 2>/dev/null
python3 v1_compare.py "$1" fixtures/v1_state_cook.json "$out/cook.json" 2>/dev/null
for case in idle cook; do
  n=$(diff fixtures/v1_baseline_${case}_original.json "$out/$case.json" | grep -c '^[<>]' || true)
  echo "v1 $case: $n differing lines (expected 6)"
done
