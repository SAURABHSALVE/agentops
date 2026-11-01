#!/usr/bin/env bash
# Checks a deployed site end to end: static page, deep link, 404, API health, and the write path through CloudFront,
# API Gateway, Lambda and DynamoDB. Usage: scripts/smoke_test.sh https://dxxxx.cloudfront.net
#
# With sign-in off, step 6 runs a real investigation. With sign-in on (the API says so in /api/config) nobody in CI can
# sign in, so step 6 checks what can be checked without an account: writes are refused, the site's sign-in settings are
# valid, Cognito's real hosted page accepts this site's client and callback, and the Lambda can reach Cognito for the keys
# it needs to check a token.
set -euo pipefail

base="${1:?usage: smoke_test.sh <site url>}"
base="${base%/}"
retry=(--silent --show-error --fail --retry 6 --retry-delay 5 --retry-all-errors --max-time 30)
fail() { echo "SMOKE TEST FAILED: $*" >&2; exit 1; }
# Status code and body of a request that is allowed to be refused (no --fail); body goes to $body.
status_of() { curl --silent --show-error --max-time 30 --retry 3 --retry-delay 5 --retry-all-errors -o "$body" -w '%{http_code}' "$@"; }
body="$(mktemp)"; trap 'rm -f "$body"' EXIT

echo "1/6 home page";            curl "${retry[@]}" "$base/" | grep -qi "agentops" || fail "home page does not mention AgentOps"
echo "2/6 deep link";            curl "${retry[@]}" "$base/incidents/INC-1001" | grep -q "INC-1001" || fail "incident page missing"
echo "3/6 unknown page is 404";  code="$(curl -s -o /dev/null -w '%{http_code}' "$base/incidents/INC-9999")"
[ "$code" = "404" ] || fail "expected 404 for an unknown page, got $code"
echo "4/6 API health";           curl "${retry[@]}" "$base/health" | grep -q '"status":"ok"' || fail "/health is not ok"
echo "5/6 incident list";        curl "${retry[@]}" "$base/api/incidents" | grep -q "INC-1001" || fail "API incident list is wrong"

auth_mode="$(curl "${retry[@]}" "$base/api/config" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("auth", {}).get("mode", "none"))')"

if [ "$auth_mode" = "cognito" ]; then
  echo "6/6 sign-in (cannot sign in from CI, so checking everything short of that)"

  code="$(status_of -X POST "$base/api/incidents/INC-1001/investigations")"
  [ "$code" = "401" ] && grep -q '"sign_in_required"' "$body" || fail "a write without signing in should be 401 sign_in_required, got $code"
  code="$(status_of "$base/api/incidents/INC-1001/investigations/latest")"
  [ "$code" = "404" ] || fail "a visitor should see no investigation (404), got $code"

  cfg="$(curl "${retry[@]}" "$base/auth-config.json")"
  hosted="$(printf '%s' "$cfg" | python3 -c 'import json,sys; c=json.load(sys.stdin); assert c["clientId"] and c["userPoolId"] and c["region"]; print(c["hostedUiUrl"])')" \
    || fail "auth-config.json is missing or incomplete"
  client="$(printf '%s' "$cfg" | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientId"])')"

  # Cognito sends a valid client and registered callback to its login page, and anything else to /error.
  authorize() { # redirect_uri -> where Cognito sends the browser
    curl --silent --max-time 30 --retry 3 --retry-delay 5 --retry-all-errors -o /dev/null -w '%{redirect_url}' --get "$hosted/oauth2/authorize" \
      --data-urlencode "client_id=$client" --data-urlencode "response_type=code" --data-urlencode "scope=openid email" \
      --data-urlencode "redirect_uri=$1" --data-urlencode "state=smoke" \
      --data-urlencode "code_challenge=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" --data-urlencode "code_challenge_method=S256"
  }
  case "$(authorize "$base/auth/callback")" in
    "$hosted"/login*) ;;
    *) fail "Cognito did not accept this site's client and callback (/auth/callback)" ;;
  esac
  case "$(authorize "$base/not-a-registered-callback")" in
    "$hosted"/error*) ;;
    *) fail "Cognito accepted a callback that was never registered" ;;
  esac

  # A well-formed token with a key id nobody has. The Lambda must fetch Cognito's keys to say no: 401 means it reached
  # them; 503 auth_unavailable means it could not.
  token="$(python3 -c '
import base64, json
b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
print(b({"alg": "RS256", "kid": "smoke-test"}) + "." + b({"sub": "x"}) + ".c2ln")')"
  code="$(status_of -H "Authorization: Bearer $token" "$base/api/incidents")"
  [ "$code" = "401" ] && grep -q '"invalid_token"' "$body" || fail "the API could not check a token (got $code); can the Lambda reach Cognito?"
else
  echo "6/6 run an investigation"
  started="$(curl "${retry[@]}" -X POST "$base/api/incidents/INC-1001/investigations")"
  id="$(printf '%s' "$started" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    state="$(curl "${retry[@]}" "$base/api/investigations/$id" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
    [ "$state" = "running" ] || break
    sleep 2
  done
  [ "$state" = "completed" ] || fail "investigation $id ended as '$state'"
fi

echo "OK: $base"
