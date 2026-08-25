#!/usr/bin/env bash
# 🔴 R6 — outbound recipients come only from comm.v_messageable_farmer.
#
# Doc 02 §4.7 and Doc 10 both call for this check by name: "Enforce this at the
# query layer. The API must never let a campaign read from core.farmer
# directly. Make it a code-review rule and add a CI grep." This is that grep.
#
# The failure mode it prevents: someone builds a segment query against the
# farmer table because it is convenient, and a campaign goes out to people who
# never consented. That is a DPDP violation and a WhatsApp ban, introduced by
# a change that looks entirely reasonable in review.
#
# Escape hatch: a line ending in `# noqa: R6` is allowed, so a legitimate
# non-messaging read (a count, an admin screen) can be marked explicitly
# rather than forcing the check to be disabled wholesale.
set -uo pipefail

TARGET="backend/apps/communications"
[ -d "$TARGET" ] || { echo "R6: $TARGET not present yet — skipping"; exit 0; }

# Patterns that indicate reading the farmer table directly from the
# messaging app: the Django model, a raw query, or the ORM manager.
PATTERN='core\.farmer|Farmer\.objects|FROM[[:space:]]+core\.farmer'

HITS=$(grep -rEn "$PATTERN" "$TARGET" \
         --include='*.py' \
         2>/dev/null | grep -v '# noqa: R6' || true)

if [ -n "$HITS" ]; then
  cat >&2 <<'MSG'
=============================================================
R6 VIOLATION — direct farmer-table access in apps/communications
=============================================================
Outbound recipients must come from comm.v_messageable_farmer,
which enforces: opted-in non-expired consent, contact point not
marked do_not_contact, fewer than 3 delivery failures, quality
tier != quarantine, not soft-deleted, and no suppression match.

Reading core.farmer directly bypasses all six.

  Doc 05 §5 R6 · Doc 10 §1 · smoke tests 6, 7, 8

If this read is genuinely not for messaging, append `# noqa: R6`
to the line with a comment saying why.
MSG
  echo "" >&2
  echo "$HITS" >&2
  exit 1
fi

echo "R6 OK — no direct farmer-table access in $TARGET"
