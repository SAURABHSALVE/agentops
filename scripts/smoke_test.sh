#!/usr/bin/env bash
# Checks a deployed site end to end: static page, deep link, 404, API health, and a real investigation
# (the write path through CloudFront, API Gateway, Lambda and DynamoDB).  Usage: scripts/smoke_test.sh https://dxxxx.cloudfront.net
set -euo pipefail

base="${1:?usage: smoke_test.sh <site url>}"
base="${base%/}"
retry=(--silent --show-error --fail --retry 6 --retry-delay 5 --retry-all-errors --max-time 30)
fail() { echo "SMOKE TEST FAILED: $*" >&2; exit 1; }

echo "1/6 home page";            curl "${retry[@]}" "$base/" | grep -qi "agentops" || fail "home page does not mention AgentOps"
echo "2/6 deep link";            curl "${retry[@]}" "$base/incidents/INC-1001" | grep -q "INC-1001" || fail "incident page missing"
echo "3/6 unknown page is 404";  code="$(curl -s -o /dev/null -w '%{http_code}' "$base/incidents/INC-9999")"
[ "$code" = "404" ] || fail "expected 404 for an unknown page, got $code"
echo "4/6 API health";           curl "${retry[@]}" "$base/health" | grep -q '"status":"ok"' || fail "/health is not ok"
echo "5/6 incident list";        curl "${retry[@]}" "$base/api/incidents" | grep -q "INC-1001" || fail "API incident list is wrong"

echo "6/6 run an investigation"
started="$(curl "${retry[@]}" -X POST "$base/api/incidents/INC-1001/investigations")"
id="$(printf '%s' "$started" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  state="$(curl "${retry[@]}" "$base/api/investigations/$id" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$state" = "running" ] || break
  sleep 2
done
[ "$state" = "completed" ] || fail "investigation $id ended as '$state'"

echo "OK: $base"
