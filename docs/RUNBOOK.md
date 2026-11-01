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
Writes are open unless an access code is set. If someone is burning your quota:
1. Set an access code (README, "Turning AWS deploys on" and the access-code section) and redeploy.
2. API Gateway throttling and the per-process rate limit are the backstops; the real cost cap for live mode is an
   **OpenAI project spend limit**, which you must set in the OpenAI dashboard before enabling live mode.
3. As a last resort run `scripts/teardown.sh --stage prod` (it asks you to type the stage name; `--include-data` also removes the table).

## Data and privacy
- Records live 7 days (DynamoDB TTL). Feedback notes are stored with the investigation and are never sent to a model.
- Telemetry text is redacted (emails, IPs, tokens, keys, passwords, card numbers) before it reaches the model or the UI.
  This is a safety net for future real log sources, not a guarantee for every secret shape.
- Rotate any key that was ever pasted into a chat, ticket or screenshot.

## Backup and restore
There is no backup, by design: the data is demo records that expire in 7 days and the infrastructure is code. To rebuild from
nothing: bootstrap CDK, deploy `AgentOps-GitHubOidc`, then push to `main` (or run the deploy workflow). **Not rehearsed on
this account**; do a rehearsal on a `dev` stage before relying on it.
