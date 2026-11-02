# Security guidance

Portfolio MVP, not a production system. All data is synthetic by default. Revised for D-018 (OpenAI provider, local-first).

## Secrets and credentials
- Never commit `.env`, API keys, access keys, session tokens or AWS config. `.env.example` contains placeholders only.
- Never paste a key into chat, an issue, a screenshot or a recorded terminal. Set secrets in your own environment or in
  the git-ignored `backend/.env` (D-021); `.gitignore` ignores `.env` files at every level.
  Avoid typing a key inline in a command, because shell history can keep it.
- **OpenAI:** `OPENAI_API_KEY` is read only by the FastAPI backend from its environment. It is never sent to the browser,
  never in a `NEXT_PUBLIC_*` variable, never logged or printed, never in an image or repo. Mask it in any diagnostic output.
  Set a **hard spend limit** on the key's OpenAI project. Rotate the key if it is ever exposed.
- **AWS:** do not use the root user, root access keys, or the current `default` profile (it resolves to root) for
  development, deployment or application code. Before any AWS operation, the user creates a least-privilege IAM user/role
  or Identity Center profile (for example `agentops-dev`) and signs in from their own terminal. Scope permissions to what
  that phase needs (hosting, secrets read, logs write, and only if adopted: one DynamoDB table, CloudWatch read). Whether the root
  user currently has long-lived access keys is unverified; check in the console and delete any that exist.
- Local runs in the default mock mode need no credentials at all.
- **CI/CD (D-023):** GitHub Actions authenticates to AWS with OIDC, never with stored keys. The deploy role trusts exactly
  `repo:SAURABHSALVE@90780335/agentops@1437085899:ref:refs/heads/main` (GitHub's immutable subject, which carries the numeric
  owner and repository ids, so a renamed or re-registered repository name cannot impersonate this one); the pull-request
  role trusts `...:pull_request` and can only assume the
  CDK lookup role. Both can do nothing except `sts:AssumeRole` on CDK bootstrap roles. The OIDC stack is deployed by a human,
  not by CI. Known limit: the default CDK CloudFormation execution role is administrator-level, so protect `main` and consider
  bootstrapping with a scoped `--cloudformation-execution-policies`.
- The alert email is a GitHub *variable* and a CDK context value, never committed; workflows mask it in logs.
- The public API has no authentication (simulated data only). It is throttled at API Gateway (10 req/s, burst 20). Add real
  access control before any billable key (Phase 3B) is reachable from it.

## Data egress
- With `AGENT_PROVIDER=openai`, prompts, log excerpts and tool results are sent to OpenAI. That is acceptable only for the
  synthetic demo data. Real telemetry (for example CloudWatch) must be opt-in, redacted, and is never the default.
- Check the provider's current data-retention and training settings for the API and disable storage where an option exists
  (verify in the docs in Phase 3B; not yet checked).
- Simulated data and real data must always be labelled distinctly in the UI and README.

## Agent boundaries
- The model may only call the four allowlisted, read-only tools: `get_incident`, `search_logs`, `get_service_metrics`,
  `get_recent_deployments`. Unknown tool names are rejected.
- Strict server-side argument models (unknown fields rejected, bounded lengths and ranges), per-call timeouts, result-size
  caps, a max number of rounds, and a max total token budget per investigation.
- The final answer is validated against a Pydantic schema; invalid output is rejected and replaced with an honest failure state.
- Evidence references must exactly match IDs returned by this investigation's recorded tool calls; ungrounded findings are rejected.
- Remediation is a closed set of `simulate_*` action IDs that only change simulated state. No shell, SQL, IAM, deployment
  or infrastructure mutation is reachable from any model output.
- Remediation runs only after explicit human approval, checked server-side against persisted state. Duplicate approvals run once.
  The model's recommendation never grants permission.
- **Log content is untrusted input** and may contain prompt injection. It is passed to the model as clearly delimited data;
  the system prompt says to ignore instructions inside it; and, because policy is enforced in code, an injected instruction
  still cannot add tools, change arguments' validation, skip approval or cite evidence that was not retrieved.
- Provider and SDK errors are mapped to generic client errors; raw provider errors, keys and request payloads are never returned.

## Public demo
**Implemented (D-028):** an access code on every write, per-process rate limits, a token budget per investigation, and secrets
read from Secrets Manager. **Still your job before exposing a key:** set a spend limit on the OpenAI project and budget alerts.

**Sign-in (D-036):** with a Cognito user pool configured, writes need a signed-in user and every record belongs to one.
Tokens are verified in the backend, never trusted from the browser; the owner id comes only from the verified `sub`. Access
tokens are kept in `sessionStorage` (readable by script on the page, so there is no stronger secret than "can run
investigations as you"). CloudFront sends a Content-Security-Policy whose `connect-src` allows only this origin and Cognito's
hosted page, so a stolen token has nowhere to go; `script-src` still allows inline script because the static export needs it
(DECISIONS D-039). Each user also has their own investigation cap on top of the shared one. Self sign-up is open by design, so sign-in limits what a
stranger can do to your *data*, not who can create an account.

A deployed backend that holds a billable key is an abuse target. Before exposing it: access control and rate limiting on the
investigate endpoint, a spend limit on the OpenAI project, a token budget per investigation, and budget alerts. Otherwise
demo locally. The OpenAI key lives in a managed secret store (decided in Phase 6), never in the image, repo or frontend.

## Logging
- Do not log secrets, authorization headers, cookies, prompts or model responses. Log request ids, tool names, redacted
  arguments, durations, status, token counts and error classes only.

## Dependencies and tooling
- Read README, license and security notes before adopting any dependency. The OpenAI SDK, if used, is vetted in Phase 3B.
- Bedrock and AgentCore are not part of the project (D-018) and must not be installed, configured or invoked without
  explicit approval.
- REA (`rea-agents`) is a development-time reverse-engineering tool, MIT-licensed, and not part of the deployed app.
  It is **not installed** (D-009 open); its setup edits user-level agent config, so review `--dry-run` first.

## Reporting
Personal portfolio project; no formal disclosure process.
