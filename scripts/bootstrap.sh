#!/usr/bin/env bash
# One-time AWS setup for this project, run by a human with an admin (non-root) identity:
#   1. installs the infra toolchain locally (npm ci, a Python venv)
#   2. `cdk bootstrap` the account/region (CDK's own deploy roles and asset bucket)
#   3. deploys AgentOps-GitHubOidc: the OIDC provider and the two GitHub Actions roles
# then prints the GitHub repository variables to set. CDK asks before creating IAM roles; read that prompt.
#
#   aws login            # first, in your own terminal
#   scripts/bootstrap.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
infra="$root/infra"
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
die() { echo "error: $*" >&2; exit 1; }

for tool in node npm python aws; do command -v "$tool" >/dev/null || die "$tool is not on PATH"; done

identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text 2>/dev/null)" \
  || die "no valid AWS credentials. Run: aws login"
account="${identity%%[[:space:]]*}"; arn="${identity#*[[:space:]]}"
case "$arn" in *:root) die "do not bootstrap as the root user ($arn). Sign in as an admin user or role." ;; esac
echo "Account $account, identity $arn, region $AWS_REGION"

cd "$infra"
npm ci --no-audit --no-fund
[ -d .venv ] || python -m venv .venv
# shellcheck disable=SC1091  # path differs per OS; both are checked
if [ -f .venv/Scripts/activate ]; then . .venv/Scripts/activate; else . .venv/bin/activate; fi
python -m pip install --quiet -r requirements.txt

echo; echo "== cdk bootstrap =="
npx cdk bootstrap "aws://$account/$AWS_REGION"

echo; echo "== deploy AgentOps-GitHubOidc (creates IAM roles: CDK will ask you to confirm) =="
npx cdk deploy AgentOps-GitHubOidc --outputs-file "$root/cdk-oidc-outputs.json"

deploy_arn="$(python -c "import json; print(json.load(open('$root/cdk-oidc-outputs.json'))['AgentOps-GitHubOidc']['DeployRoleArn'])")"
diff_arn="$(python -c "import json; print(json.load(open('$root/cdk-oidc-outputs.json'))['AgentOps-GitHubOidc']['DiffRoleArn'])")"
rm -f "$root/cdk-oidc-outputs.json"

cat <<EOF

AWS side done. Now tell GitHub (Settings > Secrets and variables > Actions > Variables), or with the gh CLI:

  gh variable set AWS_DEPLOY_ROLE_ARN --body "$deploy_arn"
  gh variable set AWS_DIFF_ROLE_ARN   --body "$diff_arn"
  gh variable set ALERT_EMAIL         --body "you@example.com"     # alarms and budget go here; never commit it

Then push to main (or run the Deploy workflow). These are variables, not secrets: nothing here is a credential.
EOF
