#!/usr/bin/env bash
# Safely removes one stage of AgentOps from AWS.
#
#   scripts/teardown.sh --stage dev [--dry-run] [--include-data] [--yes]
#   scripts/teardown.sh --stage prod [--dry-run] [--include-data]
#
# What it does, in dependency order: Monitoring (alarms, budget, SNS) -> Frontend (CloudFront, S3) ->
# Backend (Lambda, API) -> and ONLY with --include-data, Auth (the user pool: everyone's accounts) and
# Data (DynamoDB, secret slot).
#
# Safety rails:
#   * shows which AWS account and identity it is about to act on, and makes you type the stage to continue
#   * only touches stacks tagged Project=agentops, and only the ones named for the stage
#   * data (investigation history, user accounts, the OpenAI key slot) is kept unless you pass --include-data
#   * prod always needs interactive confirmation (--yes is ignored), and prod data needs a second phrase
#   * never touches the GitHub OIDC stack: it is account-level and the CI deploy role lives in it
#   * --dry-run changes nothing and prints what would happen
set -euo pipefail

REGION="us-east-1"
stage="" dry_run=0 include_data=0 assume_yes=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }
die() { echo "error: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --stage) stage="${2:-}"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --include-data) include_data=1; shift ;;
    --yes) assume_yes=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done
case "$stage" in dev|prod) ;; *) echo "--stage must be dev or prod" >&2; usage ;; esac

aws_() { aws --region "$REGION" --no-cli-pager "$@"; }

identity="$(aws_ sts get-caller-identity --query '[Account,Arn]' --output text 2>/dev/null)" \
  || die "no valid AWS credentials. Run: aws login   (or: aws sso login)"
account="${identity%%[[:space:]]*}"; arn="${identity#*[[:space:]]}"
case "$arn" in *:root) die "refusing to run as the root user ($arn). Use your admin role/user instead." ;; esac

prefix="AgentOps-$stage"
# Delete order: reverse of dependency (Monitoring and Frontend need Backend; Backend and Frontend import from Auth,
# so Auth must go after them; Data is independent).
stacks=("$prefix-Monitoring" "$prefix-Frontend" "$prefix-Backend")
[ "$include_data" -eq 1 ] && stacks+=("$prefix-Auth" "$prefix-Data")

# Keep only stacks that exist AND carry our tag.
targets=()
for s in "${stacks[@]}"; do
  if ! aws_ cloudformation describe-stacks --stack-name "$s" >/dev/null 2>&1; then
    echo "  skip   $s (not deployed)"; continue
  fi
  tag="$(aws_ cloudformation describe-stacks --stack-name "$s"         --query "Stacks[0].Tags[?Key=='Project'].Value | [0]" --output text)"
  [ "$tag" = "agentops" ] || die "$s exists but is not tagged Project=agentops; refusing to touch it."
  targets+=("$s")
done
[ "${#targets[@]}" -gt 0 ] || { echo "Nothing to remove for stage '$stage' in account $account."; exit 0; }

echo
echo "Account : $account"
echo "Identity: $arn"
echo "Region  : $REGION"
echo "Stage   : $stage"
echo "Will delete, in order:"; printf '  - %s\n' "${targets[@]}"
[ "$include_data" -eq 1 ] || echo "Keeping : $prefix-Data (DynamoDB history and the OpenAI secret slot) and $prefix-Auth (user accounts). Add --include-data to remove."
echo "Note    : CloudFront deletion can take 15+ minutes. The cost budget goes with the Monitoring stack."
echo

if [ "$dry_run" -eq 1 ]; then echo "(dry run: nothing was changed)"; exit 0; fi

if [ "$stage" = "prod" ] || [ "$assume_yes" -eq 0 ]; then
  read -r -p "Type the stage name ($stage) to confirm: " typed
  [ "$typed" = "$stage" ] || die "confirmation did not match; nothing was changed."
fi
if [ "$stage" = "prod" ] && [ "$include_data" -eq 1 ]; then
  echo "Production data and accounts are protected by stack termination protection, deletion protection and a Retain policy."
  read -r -p "Type 'delete prod data' to remove the stack's protection and delete it: " phrase
  [ "$phrase" = "delete prod data" ] || die "confirmation did not match; nothing was changed."
fi

for s in "${targets[@]}"; do
  if { [ "$s" = "$prefix-Data" ] || [ "$s" = "$prefix-Auth" ]; } && [ "$stage" = "prod" ]; then
    aws_ cloudformation update-termination-protection --stack-name "$s" --no-enable-termination-protection >/dev/null
  fi
  echo "Deleting $s ..."
  aws_ cloudformation delete-stack --stack-name "$s"
  if ! aws_ cloudformation wait stack-delete-complete --stack-name "$s"; then
    echo "$s did not delete cleanly. First failure:" >&2
    aws_ cloudformation describe-stack-events --stack-name "$s" \
      --query "StackEvents[?ResourceStatus=='DELETE_FAILED'] | [0].[LogicalResourceId,ResourceStatusReason]" --output text >&2 || true
    die "stopped; later stacks were NOT deleted. Fix the cause and re-run (it is safe to repeat)."
  fi
  echo "  deleted $s"
done

if [ "$include_data" -eq 1 ] && [ "$stage" = "prod" ]; then
  cat <<EOF

Retained on purpose (Retain policy). Remove by hand only when you are sure:
  aws dynamodb update-table --table-name agentops-prod-investigations --no-deletion-protection-enabled --region $REGION
  aws dynamodb delete-table --table-name agentops-prod-investigations --region $REGION
  aws cognito-idp list-user-pools --max-results 20 --region $REGION --query "UserPools[?Name=='agentops-prod-users']"
  (the user pool is retained with its accounts; turn off its deletion protection before deleting it)
  aws secretsmanager list-secrets --region $REGION --query "SecretList[?contains(Name,'OpenAiApiKey')].Name"
EOF
fi
echo
echo "Done. The GitHub OIDC stack (AgentOps-GitHubOidc) and the CDK bootstrap stack were left in place."
