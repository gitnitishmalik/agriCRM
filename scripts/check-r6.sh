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
# 🔴 Retargeted 30 Aug 2026, and worth understanding why. This script used to
# point at `backend/apps/communications` and, if that directory was absent,
# print "not present yet — skipping" and exit 0. When the Django service was
# retired the directory went with it, so the guard became a green tick that
# checked nothing — the worst possible state for a compliance control, because
# CI kept reporting the job as passing.
#
# It now scans the FastAPI messaging surfaces, and a missing target is
# reported as such rather than silently swallowed.
#
# Escape hatch: a line ending in `# noqa: R6` is allowed, so a legitimate
# non-messaging read (a count, an admin screen) can be marked explicitly
# rather than forcing the check to be disabled wholesale.
set -uo pipefail

# Every module that may end up choosing who receives an outbound message.
# 🔴 Add the campaign modules here the moment Phase 4 creates them. A messaging
# path this list does not name is a messaging path R6 does not cover.
TARGETS=(
  "backend/domain/delivery.py"
  "backend/domain/reminders.py"
  "backend/domain/inbound.py"
  "backend/providers/messaging.py"
  "backend/routers/deliveries.py"
  "backend/routers/inbound.py"
  "backend/routers/campaigns.py"      # Phase 4 — does not exist yet
  "backend/domain/campaigns.py"       # Phase 4 — does not exist yet
  "backend/routers/communications.py" # Phase 4 — does not exist yet
)

# Reading the farmer table directly: a raw query, or the mapped model.
PATTERN='core\.farmer|FROM[[:space:]]+core\.farmer|\bFarmer\b'

present=()
missing=()
for t in "${TARGETS[@]}"; do
  if [ -e "$t" ]; then present+=("$t"); else missing+=("$t"); fi
done

if [ ${#present[@]} -eq 0 ]; then
  echo "🔴 R6: none of the messaging targets exist. This check verified NOTHING." >&2
  echo "   Expected at least one of:" >&2
  printf '     %s\n' "${TARGETS[@]}" >&2
  echo "   Either the paths moved (fix TARGETS above) or the messaging layer" >&2
  echo "   was deleted. Both need a human." >&2
  exit 1
fi

HITS=$(grep -rEn "$PATTERN" "${present[@]}" 2>/dev/null | grep -v '# noqa: R6' || true)

if [ -n "$HITS" ]; then
  cat >&2 <<'MSG'
=============================================================
R6 VIOLATION — direct farmer-table access in a messaging path
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

echo "R6 OK — no direct farmer-table access in ${#present[@]} messaging module(s):"
printf '  %s\n' "${present[@]}"
if [ ${#missing[@]} -gt 0 ]; then
  echo "  (not yet built, will be covered when they are: ${missing[*]})"
fi
