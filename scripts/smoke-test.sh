#!/usr/bin/env bash
# Run the behavioural suite against the live schema.
#
# 🔴 Doc 02 §6 rule 7: this runs in CI on EVERY migration. It takes under a
# second and it catches trigger regressions that unit tests miss. A red smoke
# test blocks the merge — it is not advisory.
#
# The suite rolls itself back, so it is safe against a seeded dev database.
set -uo pipefail
. "$(dirname "$0")/_lib.sh"

sql_describe_target

OUT=$(sql_file smoke_test.sql 2>&1)
STATUS=$?

echo "$OUT"

if [ $STATUS -ne 0 ]; then
  echo ""
  echo "SMOKE TEST FAILED (exit $STATUS)"
  exit 1
fi

# The client can exit 0 even when an assertion inside a DO block was caught
# and handled, so assert the expected number of PASS lines as well.
PASSES=$(printf '%s\n' "$OUT" | grep -c 'PASS' || true)
EXPECTED=20

if [ "$PASSES" -ne "$EXPECTED" ]; then
  echo ""
  echo "SMOKE TEST FAILED: expected $EXPECTED assertions, saw $PASSES"
  exit 1
fi

echo ""
echo "SMOKE TEST GREEN — $PASSES/$EXPECTED assertions passed"
