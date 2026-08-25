#!/usr/bin/env bash
# End-to-end integration check: frontend proxy -> backend -> database.
#
# Every request goes through the FRONTEND origin (:5173), not the backend
# directly. That is the point — it exercises the same path a browser takes,
# including the Vite proxy that mirrors the Vercel rewrite in production. A
# test that hits :8000 directly proves the API works but not that the two
# halves are wired together.
#
# Usage:  ./scripts/verify-integration.sh
# Needs:  make up && make run   (backend on :8000)
#         npm run dev           (frontend on :5173)

set -uo pipefail

WEB="${WEB:-http://localhost:5173}"
API="${API:-http://127.0.0.1:8000}"
EMAIL="${EMAIL:-agent@agricrm.local}"
PASSWORD="${PASSWORD:-agricrm-dev-2026}"
MFA_EMAIL="${MFA_EMAIL:-ops@agricrm.local}"

PASS=0
FAIL=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n'   "$1"; printf '        %s\n' "$2"; FAIL=$((FAIL + 1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# Read a dotted key path out of a JSON body on stdin.
# Deliberately not eval-based: an argument like ['access'] contains quotes that
# break a Python string literal, which silently yields an empty result and makes
# a passing API look broken.
jqv() {
  python -c '
import sys, json
try:
    value = json.load(sys.stdin)
    for key in sys.argv[1].split("."):
        value = value[key]
except Exception:
    sys.exit(1)
print(json.dumps(value) if isinstance(value, (list, dict)) else value)
' "$1" 2>/dev/null
}

head_ "Reachability"

code=$(curl -s -o /dev/null -w '%{http_code}' "$WEB/" || echo 000)
[ "$code" = "200" ] && ok "frontend serves the SPA shell" \
                    || bad "frontend serves the SPA shell" "got HTTP $code at $WEB"

code=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/v1/healthz/" || echo 000)
[ "$code" = "200" ] && ok "backend health probe" \
                    || bad "backend health probe" "got HTTP $code at $API"

# The proxy hop is the integration itself.
body=$(curl -s "$WEB/api/v1/healthz/")
[ "$body" = '{"status":"ok"}' ] && ok "frontend proxies /api to the backend" \
                                || bad "frontend proxies /api to the backend" "got: $body"

head_ "SPA delivery"

html=$(curl -s "$WEB/")
grep -q '<div id="root">' <<<"$html" && ok "React mount point present" \
                                     || bad "React mount point present" "no #root in HTML"
grep -q 'IBM+Plex' <<<"$html" && ok "Plex font family requested" \
                              || bad "Plex font family requested" "font link missing"

# A client-side route must not 404 on a hard reload.
code=$(curl -s -o /dev/null -w '%{http_code}' "$WEB/account")
[ "$code" = "200" ] && ok "deep link /account serves the shell (SPA fallback)" \
                    || bad "deep link /account serves the shell" "got HTTP $code"

head_ "Authentication"

login=$(curl -s -X POST "$WEB/api/v1/auth/login/" \
          -H 'Content-Type: application/json' \
          -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")

ACCESS=$(jqv access <<<"$login")
REFRESH=$(jqv refresh <<<"$login")

[ -n "$ACCESS" ] && ok "login returns an access token" \
                 || bad "login returns an access token" "response: ${login:0:180}"
[ -n "$REFRESH" ] && ok "login returns a refresh token" \
                  || bad "login returns a refresh token" "response: ${login:0:180}"

role=$(jqv user.role <<<"$login")
[ "$role" = "field_agent" ] && ok "login carries the user's role ($role)" \
                            || bad "login carries the user's role" "got: $role"

districts=$(jqv user.district_ids <<<"$login")
[ "$districts" = "[9001, 9002]" ] && ok "territory returned for RLS scoping ($districts)" \
                                  || bad "territory returned for RLS scoping" "got: $districts"

head_ "Authorisation"

me=$(curl -s "$WEB/api/v1/auth/me/" -H "Authorization: Bearer $ACCESS")
name=$(jqv full_name <<<"$me")
[ -n "$name" ] && ok "authenticated request to /auth/me/ succeeds ($name)" \
               || bad "authenticated request to /auth/me/" "response: ${me:0:180}"

code=$(curl -s -o /dev/null -w '%{http_code}' "$WEB/api/v1/auth/me/")
[ "$code" = "401" ] && ok "unauthenticated request is rejected (401)" \
                    || bad "unauthenticated request is rejected" "got HTTP $code"

# Doc 11 §1 fixes the code per status; DRF's own is more granular.
err=$(curl -s "$WEB/api/v1/auth/me/" | jqv error.code)
[ "$err" = "unauthenticated" ] && ok "error envelope matches Doc 11 (code=$err)" \
                              || bad "error envelope matches Doc 11" "got code: $err"

bad_login=$(curl -s -X POST "$WEB/api/v1/auth/login/" \
              -H 'Content-Type: application/json' \
              -d "{\"email\":\"$EMAIL\",\"password\":\"wrong-password\"}")
err=$(jqv error.code <<<"$bad_login")
[ "$err" = "unauthenticated" ] && ok "wrong password rejected with the same envelope" \
                              || bad "wrong password rejected" "got: ${bad_login:0:180}"

head_ "Token lifecycle"

refreshed=$(curl -s -X POST "$WEB/api/v1/auth/refresh/" \
              -H 'Content-Type: application/json' \
              -d "{\"refresh\":\"$REFRESH\"}")
NEW_ACCESS=$(jqv access <<<"$refreshed")
[ -n "$NEW_ACCESS" ] && ok "refresh token mints a new access token" \
                     || bad "refresh token mints a new access token" "response: ${refreshed:0:180}"

NEW_REFRESH=$(jqv refresh <<<"$refreshed")
[ -n "$NEW_REFRESH" ] && [ "$NEW_REFRESH" != "$REFRESH" ] \
  && ok "refresh tokens rotate (Doc 12 §1)" \
  || bad "refresh tokens rotate" "old and new refresh tokens match, or none returned"

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/v1/auth/logout/" \
        -H 'Content-Type: application/json' \
        -H "Authorization: Bearer $NEW_ACCESS" \
        -d "{\"refresh\":\"$NEW_REFRESH\"}")
[ "$code" = "205" ] && ok "logout blacklists the refresh token (205)" \
                    || bad "logout blacklists the refresh token" "got HTTP $code"

after=$(curl -s -X POST "$WEB/api/v1/auth/refresh/" \
          -H 'Content-Type: application/json' \
          -d "{\"refresh\":\"$NEW_REFRESH\"}")
code=$(jqv error.code <<<"$after")
[ -n "$code" ] && ok "blacklisted refresh token is refused after logout" \
               || bad "blacklisted refresh token is refused" "it still worked: ${after:0:120}"

head_ "MFA gate (Doc 12 §1)"

mfa=$(curl -s -X POST "$WEB/api/v1/auth/login/" \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"$MFA_EMAIL\",\"password\":\"$PASSWORD\"}")
required=$(jqv mfa_required <<<"$mfa")
[ "$required" = "True" ] && ok "privileged role reports mfa_required" \
                         || bad "privileged role reports mfa_required" "got: $required"

enforced=$(jqv user.mfa_enforced <<<"$mfa")
[ "$enforced" = "True" ] && ok "mfa_enforced derived from role, not stored input" \
                         || bad "mfa_enforced derived from role" "got: $enforced"

cross=$(jqv user.is_cross_territory <<<"$mfa")
[ "$cross" = "True" ] && ok "data_ops is cross-territory" \
                      || bad "data_ops is cross-territory" "got: $cross"

head_ "API contract"

code=$(curl -s -o /dev/null -w '%{http_code}' "$WEB/api/schema/")
[ "$code" = "200" ] && ok "OpenAPI schema is served" \
                    || bad "OpenAPI schema is served" "got HTTP $code"

printf '\n────────────────────────────────────────\n'
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32m%d checks passed, 0 failed.\033[0m\n\n' "$PASS"
  exit 0
fi
printf '\033[31m%d passed, %d FAILED.\033[0m\n\n' "$PASS" "$FAIL"
exit 1
