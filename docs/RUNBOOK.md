# Runbook: operating the deployed demo

For whoever is on call for the live site (today: the owner). Everything here was checked against the code in this
repository; items that need you to be signed in to AWS are marked **(console)**. Nothing in this file needs the root user.

## What is running
| Piece | Where | Notes |
|---|---|---|
| Site and API | CloudFront, one URL. `/api/*` goes to API Gateway then Lambda; everything else is the static site in S3 | Region `us-east-1` |
| Data | DynamoDB table, records expire after 7 days | Investigations, approvals, feedback |
| Secrets | Secrets Manager: access code, OpenAI key (live mode only) | Never printed, never in logs |
| Alerts | CloudWatch alarms and a $10 monthly budget, emailed through one SNS topic | The email subscription must be confirmed once |
| Deploys | Push to `main`: checks, then deploy, then smoke test | A pull request runs checks and `cdk diff` only |

## Is it up? (30 seconds)
1. Open the site URL. The home page must load and the header must say "Simulated demo telemetry".
2. `GET /health` returns `{"status":"ok", ...}`. Every response has an `X-Request-Id` header.
3. Click the demo button, run an investigation, wait for "Finished after N checks".

`scripts/smoke_test.sh <site-url>` does the same from a terminal and is what CI runs after each deploy.

## Something is wrong: find the request
Every API response carries `X-Request-Id`. Each request also writes one log line in the Lambda log group
**(console: CloudWatch, Log groups)**:

    request 3f9a1c2b7d4e8a10 POST /api/incidents/INC-1001/investigations -> 202 in 41 ms

Search the log group for that id. The line never contains query strings, request bodies or the access code.
Server errors return a generic message to the user and log only the error *type*; the stack trace is not exposed.

## Alarms and what to do
| Alarm | Meaning | First step |
|---|---|---|
| `LambdaErrors` | The function crashed or timed out | Open the log group, look at the most recent `unhandled error` or `Task timed out` line |
| `Api5xx` | The API returned a 5xx (includes handled 500s) | Same log group; find the `-> 500` line and its request id |
| `LambdaThrottles` | Not enough concurrency | Unlikely at demo scale; check for a traffic spike or a loop |
| `LambdaDurationNearTimeout` | p95 is above 80% of the timeout | A slow model call or a cold DynamoDB; re-run the smoke test; consider the live-model timeout |
| Budget | Spend is over a threshold | Check CloudWatch and Lambda invocations for abuse; set the access code; see "Abuse" |

## Common problems
- **An investigation shows "failed".** The page now explains why in plain words. With the mock provider this is rare; with the
  live model a slow provider day can end a run (`provider_error`) or the model can be rejected for citing evidence it never
  fetched (`ungrounded_evidence`). Both are by design: the app refuses to show an unsupported answer. Re-run it.
- **"A newer investigation exists; approve from the latest one."** Another run finished after the one on screen. Reload.
- **First request after a quiet period takes 3 to 4 seconds.** Lambda cold start (measured 4 s once, then about 0.6 s warm).
- **A deploy failed on "IAM changes".** The pipeline refuses IAM-broadening changes on a push. Run the deploy workflow
  manually and tick "Let this run apply IAM-broadening changes" after reading the `cdk diff`.
- **A deploy failed with "Not authorized to perform sts:AssumeRoleWithWebIdentity".** The OIDC trust does not match the
  repository (see DECISIONS D-032). Redeploy `AgentOps-GitHubOidc` with an admin (non-root) identity.

## Abuse and cost
Once the Auth stack is deployed, every write needs a signed-in user; sign-up is open, so anyone with an email can get an
account. The access code and its secret exist **only in live mode** (`-c agentProvider=openai`, infra `backend_stack.py`);
the mock deployment has none, and a mock investigation costs only a few Lambda milliseconds. If someone is hammering it:
1. API Gateway throttling and the per-process rate limit are the backstops. Check the Lambda invocation graph **(console)**.
2. Before ever enabling live mode, set an **OpenAI project spend limit** in the OpenAI dashboard: it is the real cost cap.
   Live mode also creates the access code, which then gates every write.
3. Find and disable an abusive account **(console, Cognito, user pool `agentops-prod-users`, Users)**: *Disable user* stops
   new sign-ins and their token stops working within the hour. To close sign-up entirely, redeploy with a pool that has
   self sign-up off (change `auth_stack.py`), or `-c auth=none` to remove sign-in (which re-opens writes).
4. As a last resort run `scripts/teardown.sh --stage prod` (it asks you to type the stage name; `--include-data` also removes the table).

## Data and privacy
- Records live 7 days (DynamoDB TTL). Feedback notes are stored with the investigation and are never sent to a model.
- Telemetry text is redacted (emails, IPs, tokens, keys, passwords, card numbers) before it reaches the model or the UI.
  This is a safety net for future real log sources, not a guarantee for every secret shape.
- Rotate any key that was ever pasted into a chat, ticket or screenshot.

## Backup and restore
There is no backup, by design: the data is demo records that expire in 7 days and the infrastructure is code. To rebuild from
nothing: bootstrap CDK, deploy `AgentOps-GitHubOidc`, then push to `main` (or run the deploy workflow). **Not rehearsed on
this account**; do a rehearsal on a `dev` stage before relying on it.

## Sign-in
- **Users** live in the Cognito user pool `agentops-<stage>-users` (console, Cognito). Their investigations are in the
  DynamoDB table under the user's `sub` (a UUID shown on the user's page), and expire with the 7-day TTL.
- **"Sign in" does nothing / "Sign-in is not set up on this site":** the site could not read `/auth-config.json` (written by the
  Frontend stack deploy). Open `<site>/auth-config.json`; it must show `region`, `userPoolId`, `clientId`, `hostedUiUrl`.
- **Redirected back with "redirect_mismatch" on the hosted page:** the app client's callback URL must be exactly
  `<site>/auth/callback` (and the logout URL `<site>/`). They are set by the Frontend stack from the CloudFront address.
- **401 `invalid_token` on every call after signing in:** the Lambda trusts a different pool than the browser signed in to
  (`COGNITO_USER_POOL_ID` in the Lambda environment vs the pool in `auth-config.json`), or the Lambda cannot reach
  `cognito-idp.us-east-1.amazonaws.com` to fetch the signing keys (the logs then say "could not refresh signing keys" and the
  API answers 503 `auth_unavailable`).
- **No verification email:** Cognito's built-in sender allows about 50 a day and often lands in spam. Resend from the hosted
  page; do not bulk-confirm users from the console, because that skips proving the email address.
- **"Incident not found" on a real incident right after signing in (fixed 2026-11-02):** CloudFront has a distribution-wide custom
  error response, so *every* 404 (the API's too) is answered with the site's HTML 404 page. The status survives, the JSON
  error body does not. `GET .../investigations/latest` answers 404 for "you have not investigated this yet", which is normal
  for a new account. The API now answers `200 null` for that case, so first-time visitors no longer see a failed request in their
  console. **Remember this when adding an API route that returns 404 on purpose**:
  clients must not depend on its `error.code`. `scripts/e2e-auth-harness.mjs` reproduces this behaviour.
- **Removing sign-in:** redeploy with `-c auth=none`. The pool is **kept** in prod (retained); delete it by hand only when
  you are sure, after switching off its deletion protection.

## The live AI
Default is the offline mock. To turn on the real model (spends OpenAI tokens, so do these in order):
1. **OpenAI dashboard:** set a project **spend limit** first. It is the real cost cap.
2. **Store the key by hand** (never in chat, git or CI): in your own terminal with an admin (non-root) identity,
   `aws secretsmanager put-secret-value --secret-id <OpenAiSecretName output of AgentOps-prod-Data> --secret-string file://key.txt`
   then delete `key.txt`.
3. **GitHub repository variables:** `AGENT_PROVIDER=openai` (and optionally `OPENAI_MODEL`).
4. **Deploy manually** (Actions, Deploy, Run workflow) and tick *Let this run apply IAM-broadening changes*: live mode adds read
   access to two named secrets and an access code, which the pipeline refuses to grant on its own. Read the `cdk diff` first.
5. Live mode adds a shared **access code** (Secrets Manager `AccessCode`), required on top of signing in. Per-user caps drop to
   3 a minute and 20 an hour.
To switch back: delete or reset `AGENT_PROVIDER` and redeploy. A push to `main` with `AGENT_PROVIDER=openai` set but no manual
approval fails safe at the IAM check instead of going live.
