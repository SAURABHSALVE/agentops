#!/usr/bin/env bash
# Exercises teardown.sh against a stub `aws` that records every call. Nothing here reaches AWS.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
cat > "$work/bin/aws" <<'STUB'
#!/usr/bin/env bash
args="$*"; echo "$args" >> "$STUB/calls.log"
case "$args" in
  *"sts get-caller-identity"*) [ -f "$STUB/nocreds" ] && exit 255; cat "$STUB/identity" ;;
  *"cloudformation describe-stacks"*)
    name="$(sed -E 's/.*--stack-name ([^ ]+).*/\1/' <<<"$args")"
    line="$(grep "^$name:" "$STUB/stacks")" || exit 255
    case "$args" in *--query*) echo "${line#*:}" ;; esac ;;
esac
exit 0
STUB
chmod +x "$work/bin/aws"
export PATH="$work/bin:$PATH" STUB="$work/stub"

pass=0 failed=0
reset() {
  rm -rf "$STUB"; mkdir -p "$STUB"; : > "$STUB/calls.log"
  printf '123456789012\tarn:aws:iam::123456789012:user/admin\n' > "$STUB/identity"
  printf 'AgentOps-dev-Monitoring:agentops\nAgentOps-dev-Frontend:agentops\nAgentOps-dev-Backend:agentops\nAgentOps-dev-Auth:agentops\nAgentOps-dev-Data:agentops\nAgentOps-prod-Monitoring:agentops\nAgentOps-prod-Frontend:agentops\nAgentOps-prod-Backend:agentops\nAgentOps-prod-Auth:agentops\nAgentOps-prod-Data:agentops\n' > "$STUB/stacks"
}
deleted() { grep -o 'delete-stack --stack-name [^ ]*' "$STUB/calls.log" | awk '{print $3}' | paste -sd, -; }
check() { # name, expected, actual
  if [ "$2" = "$3" ]; then pass=$((pass+1)); else failed=$((failed+1)); printf 'FAIL %s\n  expected: %s\n  actual:   %s\n' "$1" "$2" "$3"; fi
}
run() { out="$(bash "$here/teardown.sh" "$@" 2>&1)"; code=$?; }

reset; run --stage dev --dry-run
check "dry run deletes nothing" "" "$(deleted)"; check "dry run exits 0" 0 "$code"
grep -q "Keeping : AgentOps-dev-Data" <<<"$out"; check "dry run says data is kept" 0 $?
grep -q "AgentOps-dev-Auth (user accounts)" <<<"$out"; check "dry run says accounts are kept" 0 $?

reset; run --stage dev --yes
check "dev --yes deletes the three non-data stacks in order, keeping accounts" "AgentOps-dev-Monitoring,AgentOps-dev-Frontend,AgentOps-dev-Backend" "$(deleted)"

reset; run --stage dev --yes --include-data
check "--include-data adds Auth (after what imports it) and then Data" "AgentOps-dev-Monitoring,AgentOps-dev-Frontend,AgentOps-dev-Backend,AgentOps-dev-Auth,AgentOps-dev-Data" "$(deleted)"

reset; out="$(echo nope | bash "$here/teardown.sh" --stage dev 2>&1)"; code=$?
check "wrong confirmation deletes nothing" "" "$(deleted)"; check "wrong confirmation fails" 1 "$code"

reset; out="$(echo dev | bash "$here/teardown.sh" --stage dev 2>&1)"
check "typed confirmation proceeds" "AgentOps-dev-Monitoring,AgentOps-dev-Frontend,AgentOps-dev-Backend" "$(deleted)"

reset; out="$(echo nope | bash "$here/teardown.sh" --stage prod --yes 2>&1)"; code=$?
check "--yes does not skip the prod prompt" "" "$(deleted)"; check "prod wrong answer fails" 1 "$code"

reset; out="$(printf 'prod\nwrong\n' | bash "$here/teardown.sh" --stage prod --include-data 2>&1)"; code=$?
check "prod data needs the second phrase" "" "$(deleted)"
grep -q update-termination-protection "$STUB/calls.log"; check "protection untouched without the phrase" 1 $?

reset; out="$(printf 'prod\ndelete prod data\n' | bash "$here/teardown.sh" --stage prod --include-data 2>&1)"
check "prod data deleted with both confirmations" "AgentOps-prod-Monitoring,AgentOps-prod-Frontend,AgentOps-prod-Backend,AgentOps-prod-Auth,AgentOps-prod-Data" "$(deleted)"
grep -q "update-termination-protection --stack-name AgentOps-prod-Data" "$STUB/calls.log"; check "prod data protection lifted first" 0 $?
grep -q "update-termination-protection --stack-name AgentOps-prod-Auth" "$STUB/calls.log"; check "prod accounts protection lifted first" 0 $?
grep -q "dev" <<<"$(grep -c 'AgentOps-dev' "$STUB/calls.log" | grep -v '^0$')"; check "prod run never touches dev stacks" 1 $?

reset; printf '123456789012\tarn:aws:iam::123456789012:root\n' > "$STUB/identity"; run --stage dev --yes
check "refuses the root user" "" "$(deleted)"; grep -q "root user" <<<"$out"; check "root refusal explained" 0 $?

reset; sed -i 's/^AgentOps-dev-Frontend:agentops/AgentOps-dev-Frontend:None/' "$STUB/stacks"; run --stage dev --yes
check "untagged stack is refused, nothing deleted" "" "$(deleted)"; check "untagged stack fails" 1 "$code"

reset; : > "$STUB/stacks"; run --stage dev --yes
check "nothing deployed is a clean no-op" 0 "$code"; grep -q "Nothing to remove" <<<"$out"; check "no-op says so" 0 $?

reset; touch "$STUB/nocreds"; run --stage dev --yes
check "no credentials fails clearly" 1 "$code"; grep -q "aws login" <<<"$out"; check "tells the user how to sign in" 0 $?

reset; run --stage staging; check "unknown stage rejected" 2 "$code"
reset; run; check "stage is required" 2 "$code"

echo "teardown.sh: $pass passed, $failed failed"
[ "$failed" -eq 0 ]
