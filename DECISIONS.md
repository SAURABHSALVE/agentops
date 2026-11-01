# Decision log

Format: ID · status · decision · why · alternatives rejected. Status is `proposed` until the phase is approved; `superseded` entries are kept for history.

**D-001 · proposed · Next.js + TypeScript + Tailwind for the UI, FastAPI + Pydantic for the API.**
Mandated by the brief. Pydantic gives schema validation for both API and model output.

**D-002 · superseded by D-018 · Bedrock Converse API with a hand-written bounded tool loop in FastAPI.**
The hand-written bounded loop is retained (now provider-agnostic, D-019); the Bedrock part is dropped.
Keeps the loop small, testable, and inspectable (max rounds, per-tool timeout, output size caps).
Rejected for now: AgentCore (availability/benefit unverified; won't be claimed unless actually integrated), LangChain-style frameworks (extra dependency, less control).

**D-003 · proposed · Repository interface with a local store as default; DynamoDB optional.**
Protects the schedule if AWS setup stalls. The UI/README must state the real storage mode.

**D-004 · proposed · No Step Functions / Lambda / S3 unless they do real work.**
Sequential workflow fits a backend state machine. Avoids keyword padding.

**D-005 · proposed · Polling, not streaming, for investigation progress.**
Lower risk in four days; honest progress state in UI.

**D-006 · proposed · Deterministic fixtures for all telemetry; labelled "simulated demo telemetry" in UI and README.**
Repeatable tests and evals. Never presented as real CloudWatch data.

**D-007 · superseded by D-018/D-019 · Model ID and region come from environment config only.**
The principle (model identity lives in env config, never hard-coded) is retained as `OPENAI_MODEL`. The Bedrock inference-profile candidate is withdrawn; no model is selected until verified in Phase 3B.

**D-008 · proposed · Backend enforces policy; model only recommends.**
Tool allowlist, argument validation, server-side approval state, allowlisted remediation IDs, evidence IDs checked against recorded tool results.

**D-009 · OPEN · How to install REA (development-time tool only).**
Dry-run of `npx rea-agents@6.8.0 setup --client claude_code` shows it would (a) add `mcpServers.rea` to the *global* `~/.claude.json` (backup: `~/.claude.json.rea.backup`) and (b) install a skill in `~/.claude/skills/reverse-engineer-anything`. That affects every project.
Options: (1) global as documented; (2) separate profile via `CLAUDE_CONFIG_DIR`; (3) defer until a task needs it. REA is **not** part of the deployed runtime. Awaiting user choice.

**D-011 · accepted (Phase 1 approved) · Frontend lives in `frontend/` (Next.js 16.4.3, React 19, Tailwind 4, App Router, Cache Components on).**
Scaffolded with `create-next-app`, which enables `cacheComponents`. Consequence: `dynamicParams` is unavailable, so unknown incident IDs render the not-found UI via `notFound()` but return HTTP 200 (streamed), not 404. Revisit when the page fetches from the API in Phase 2.

**D-012 · accepted (Phase 1 approved) · Phase 1 types use snake_case and mirror the planned API schema; the mock flow is a pure reducer.**
`frontend/src/lib/types.ts` matches the structured-response spec so real API data can replace mock data without mapping. `investigation-reducer.ts` ignores duplicate approve/reject; in later phases the backend owns these states and must enforce them itself.

**D-013 · proposed · REA not installed.** Skipped for now (D-009 option 3); nothing in Phase 1 needed it.

**D-014 · accepted (Phase 2 approved) · Diagnostic tools are scoped by `incident_id`, not by free-form service name.**
`search_logs`, `get_service_metrics`, `get_recent_deployments` derive the service and the "now" time from the incident, so the agent can only look inside the incident it was given. Consequence: the argument strings shown in the Phase 1 mock UI (`service="checkout-api"`) will change when the UI is wired to real tool calls.

**D-015 · accepted (Phase 2 approved) · The answer key (`simulator/ground_truth.py`) is separate from the data and never imported by app code.**
A test fails if anything under `app/` imports it, and another checks API responses never contain it. Used only by tests and the Phase 7 evaluation.

**D-016 · accepted (Phase 2 approved) · Remediation execution exists as a function but has no HTTP route until Phase 5.**
`Simulator.execute_remediation` only accepts allowlisted `simulate_*` ids and just flips in-memory state; a test asserts no write routes are exposed. The approval gate that must precede it is Phase 5 work.

**D-017 · accepted (Phase 2 approved) · Backend dependencies: fastapi 0.162.0, uvicorn 0.54.0, pydantic 2.14.1 (runtime); pytest, httpx, ruff, mypy (dev).**
Licenses checked (MIT / BSD-3). `requirements.txt` pins direct runtime deps; `requirements-dev.txt` is a full freeze. Starlette's TestClient prints a deprecation warning recommending `httpx2`; I did not adopt that package without vetting it.

**D-010 · accepted (Phase 2 approved) · Python 3.14 is acceptable.** A cp314 `pydantic-core` wheel exists. Confirmed in Phase 2 by a clean-room install and 84 passing tests.

**D-018 · proposed · OpenAI is the model provider, the project is local-first, and Bedrock and AgentCore are removed from the MVP.**
Date 2026-10-28; this is a user decision.
- *Why:* the user already has an OpenAI API key. This takes AWS model-access enablement, model/region availability checks and model-call IAM scoping off the critical path, lets Phases 3 to 5 run entirely on a laptop, and protects the four-day timebox. AWS stays the intended deployment target where it adds real value (hosting, secrets, logging, IAM, optionally DynamoDB and CloudWatch).
- *Trade-offs:* (+) fewer moving parts and faster iteration; the tool-calling pattern is the same. (-) OpenAI usage is billed by OpenAI, so AWS credits should not be assumed to cover it. (-) Prompts and tool outputs leave AWS for a third party; acceptable only because the MVP data is synthetic, and real CloudWatch data would need explicit opt-in and redaction. (-) A less AWS-native portfolio story, to be described honestly without overclaiming. (-) A billable secret must be protected in any public deployment. (-) Another vendor to depend on, and its current API/SDK must be read and vetted before use.
- *Reconsider (Bedrock or another provider) if:* AWS-credit coverage of model spend becomes decisive; real telemetry must stay inside AWS; the target audience weights AWS-native AI services heavily; OpenAI quota, cost or reliability blocks the demo; or a compliance requirement appears. That needs explicit user approval.
- *Until then:* Bedrock and AgentCore are not installed, configured or invoked. The Phase 0 listing of Bedrock models in us-east-1 never involved a model call.
- *Rejected:* keeping Bedrock (setup friction; user's choice); supporting several real providers now (extra surface; only the mock and OpenAI are built, and the small interface in D-019 keeps the door open).

**D-019 · proposed · A small provider abstraction with a deterministic mock as the default.**
One `LLMProvider` interface with a single `complete(...)` call; implementations `MockProvider` (default: no network, no credentials, rule-based over tool results, never reads the answer key, labelled "mock / deterministic, not an LLM") and `OpenAIProvider` (opt-in via `AGENT_PROVIDER=openai`, key in the backend environment only); plus a test-only `ScriptedProvider` for malformed, malicious and failing responses. No agent framework.
- *Bounds:* max rounds, per-request timeout, max output tokens, max total tokens per investigation, at most one retry on transient provider errors only, tool result size caps.
- *Trust rules:* the final answer is always re-validated with Pydantic; evidence IDs must match recorded tool results; log text is delimited untrusted data; the backend, not the model, enforces the allowlist and approval.
- *Model:* `OPENAI_MODEL` from env; not chosen until verified against current docs in Phase 3B. Use structured outputs / function calling where supported, never as a substitute for server-side validation.
- *Rejected:* an SDK-shaped abstraction with many knobs (over-engineering); calling the SDK directly from the coordinator (untestable offline).

**D-020 · proposed · AWS is for deployment and optional integrations only; least-privilege IAM is a prerequisite.**
- Phases 3 to 5 need no AWS credentials. The root user must never be used for development, deployment or application code, and no root access keys may be used (whether root currently has access keys is unverified; the user should check the console and remove any).
- Before any AWS operation (Phase 6, optional DynamoDB or CloudWatch) the user creates a least-privilege IAM user/role or Identity Center profile and runs the sign-in commands in their own terminal; the assistant never handles keys.
- *DynamoDB:* optional backend, evaluated after Phase 4's local store; adopted only if setup is straightforward and it materially improves the demo; never a blocker for local development.
- *Local persistence (Phase 4):* SQLite via the standard library (decided in D-021).
- *CloudWatch:* optional read-only real telemetry, never the default. The deterministic simulator stays the default source and simulated data is always labelled as such.
- *Hosting platform:* not chosen now; evaluated in Phase 6 against permissions, region availability, cost and credit coverage, then recorded as its own decision.

**D-021 · accepted (user, 2026-10-28) · Phase 3 split, secrets handling, local persistence, UI copy, and AWS scope.**
1. **Phase 3 is split.** *3A* is fully offline: provider interface, `MockProvider` (and the test-only `ScriptedProvider`), the bounded investigation loop, investigation API endpoints, and the UI wired to the API. *3B* is the live OpenAI integration. Each has its own review gate.
2. **Local secrets live in a git-ignored `backend/.env`.** A conventional environment loader (for example `python-dotenv`) is added only if it proves necessary, and is vetted (license, README, security notes) before adoption; otherwise the shell environment is used. The key is never exposed to the browser, printed, logged or committed. (`.gitignore` already ignores `.env` at every level; verified for `backend/.env`.)
3. **Phase 4 local persistence is SQLite via Python's standard library**, behind the existing `IncidentRepository` interface. JSON-file storage is rejected. No new dependency.
4. **The stale Bedrock UI copy** in `frontend/src/app/page.tsx` is corrected in the next relevant frontend change (3A, when the UI is wired to the API), not as a separate edit.
5. **Bedrock, AgentCore and AWS resource creation stay out of scope** until explicitly approved. AWS deployment is evaluated in Phase 6 using least-privilege credentials. This reaffirms D-018 and D-020.

**D-022 · (a) and (b) resolved 2026-10-30 · Remaining Phase 3/5/6 questions.**
(a) RESOLVED: new incidents, not variants. INC-1005 (prompt injection) and INC-1006 (action succeeds, verification fails) were added; INC-1001 to INC-1004 are unchanged. The user did not name a choice and asked to proceed, so the recommended option was used.
(a) New fixtures are needed without altering INC-1001 to INC-1004 expectations: a prompt-injection log (3A) and a remediation-succeeds-but-verification-fails scenario (Phase 5). Decide whether these are new incidents (for example INC-1005/INC-1006) or variants of existing ones. (b) RESOLVED by me, not by the user, when asked to make it production-ready: a shared access code on every write plus rate limits (D-028). Reads stay open because they serve only simulated data. Change it if you prefer an allow-list or a local-only demo.

**D-023 · accepted (user, 2026-10-30) · AWS hosting: static site on S3 + CloudFront, API on Lambda + API Gateway HTTP API, investigation state in DynamoDB. Infrastructure as Python CDK in `infra/`.**
1. **Lambda over a container.** Idle cost is about zero and it matches a portfolio audience. The cost is that state cannot live in the container, so investigations move to DynamoDB (`DynamoDbInvestigationStore`, same `InvestigationStore` interface, selected by `STORAGE_MODE=dynamodb`). This pulls the cloud half of Phase 4 forward; local SQLite is still planned.
2. **Investigations run inside the POST request on Lambda.** Mangum returns the response only after Starlette background tasks finish, so the investigation completes within the invocation (the 202 body is still the `running` snapshot, and clients poll as before). That is fine for the deterministic mock (milliseconds) and bounded by the 29s Lambda timeout under API Gateway's 30s limit. **Phase 3B (live OpenAI) must revisit this** (asynchronous self-invoke, or a queue), because several model rounds can exceed 30s.
3. **Duplicate-start protection is a conditional write** (`LOCK#<incident>` item with a 60s expiry), so it holds across containers. A run whose container died is reported as `failed` on the next read rather than polling forever.
4. **Bedrock and AgentCore stay out** (D-018 reaffirmed). The only model-related AWS resource is an empty Secrets Manager slot for `OPENAI_API_KEY`, which nothing reads yet.
5. **New dependencies, vetted:** `mangum` 0.22.0 (MIT, Lambda adapter, imported only by `app/lambda_handler.py`), `boto3` 1.43.122 (Apache-2.0; Lambda supplies its own copy), `moto` 5.2.5 (Apache-2.0, dev only; its tests skip on Python 3.15 until PyYAML ships a wheel).
6. **Lambda runtime is Python 3.13**, with CI on the same version, so the deployed interpreter is the tested one.

**D-024 · accepted (user, 2026-10-30) · Frontend is a Next.js static export; Cache Components and partial prefetching are switched off. Amends D-011.**
`output: "export"` with `trailingSlash: true` produces plain files for S3 + CloudFront. Next.js 16.4.3 refuses the export while `cacheComponents` and `partialPrefetching` are on ("PPR cannot be enabled in export mode"). Nothing in `src/` used a caching API (they were `create-next-app` defaults), so they were removed. Consequences: unknown incident IDs now get a real 404 from the CDN instead of a streamed 200 (the D-011 caveat goes away); `generateStaticParams` still enumerates the four simulated incidents. If a route later needs request-time rendering, this decision must be revisited (Amplify Hosting or a server). `npm run typecheck` runs `next typegen` first because `PageProps`/`LayoutProps` are generated types, so a clean checkout cannot run bare `tsc`.

**D-025 · accepted (user, 2026-10-30) · Phases 3A to 5 built together; AWS deployment paused. The app must work fully locally.**
The user asked to complete the phases through 5 and test the whole app locally, with AWS only for later deployment. The AWS code (D-023, D-024) is left as is and was not touched or deployed.

**D-026 · accepted · OpenAI provider uses Chat Completions over the standard library, no SDK.**
- *Why:* the official docs site could not be fetched here, so request and response shapes (tool_calls with JSON-string arguments, strict `json_schema` response format, `usage`) were verified against the live API with the user's key. Zero new dependencies keeps the supply chain and Lambda package small. The endpoint is a fixed constant, so configuration cannot redirect the key.
- *Model:* `gpt-4.1-mini`, chosen from the account's model list for low cost and plain function-calling semantics. It comes from `OPENAI_MODEL`, never hard-coded.
- *Trade-offs:* we own the wire format and must re-verify it if the API changes (covered by offline contract tests with a fake transport). Provider error bodies are deliberately never surfaced, because they can echo parts of the key.
- *Prompt:* the model is shown the real remediation allowlist (and the schema narrows `remediation_action_id` to it) because without it the live model invented action ids and was correctly rejected by the backend.

**D-027 · accepted · Approval gate and persistence design.**
- Approve and reject take no request body; the action id is read from the validated findings stored server-side, so a client cannot choose an action. Approval runs the simulated action once under a lock, then verification as a separate state (`resolved`, `not_resolved`, `not_verifiable`); repeated approvals return the recorded outcome. Only the newest investigation of an incident can be approved.
- Local persistence is one SQLite file (standard library). `create` runs in an IMMEDIATE transaction so one running investigation per incident is atomic; runs interrupted by a restart are marked failed at start-up.
- The timeline is derived from recorded investigation data instead of a separate event table, so it cannot disagree with the records and works with any store. The DynamoDB store shows only the newest run (no per-incident index).
- Rejected: a client-supplied action id; a separate mutable event log.

**D-028 · accepted · Public-demo protection: access code on writes, per-process rate limits, secrets from Secrets Manager.**
- Start, approve and reject require `X-Access-Code` when a code is configured (constant-time compare). A structural test fails if any future write route lacks the gate; it caught that approve/reject were initially unguarded. The limiter runs before a record is created, so a refused request never leaves an investigation stuck `running`.
- The limiter is per process (so a backstop, not a global cap). The real cost controls are the API Gateway throttle and a spend limit on the OpenAI project.
- The OpenAI key and the access code are read from Secrets Manager at cold start when `OPENAI_SECRET_ARN` / `ACCESS_SECRET_ARN` are set; failures never echo AWS error text.
- CDK: live mode is opt-in (`-c agentProvider=openai`); the default synth is unchanged. Rejected: always-on live mode (it would change existing deployments) and a WAF (costs more per month than the rest of the stack).

**D-029 · accepted · Deployment was not performed: the only AWS identity is the root user.**
SECURITY.md and D-020 forbid root for deployment. Verified read-only (STS identity is root); nothing was created. The deployment code is tested (35 template tests, strict cdk-nag synth in both modes) but unverified against a real account until a least-privilege identity is provided.


**D-030 · accepted · Real-telemetry mode: a lab system measured into a dataset, behind a `TelemetrySource` seam.**
- *Why:* the user asked for the product to feel real. Real production data is out of reach (no access, privacy, no AWS identity that is not root), and Docker is not available here, so the OpenTelemetry demo was not an option. A small real system (`backend/lab`: HTTP dependencies, a real SQLite database, a real queue) is run with faults injected and its logs and metrics are measured into `data/captured/lab-capture.json`.
- *Seam:* the four diagnostic tools read through `TelemetrySource` (`FixtureSource` is the unchanged default, `CapturedSource` the new one, selected by `TELEMETRY_DATASET`). All enforcement (allowlist, argument validation, size caps, grounding, approval) sits above it, so a new source cannot widen what the agent may do. The 305 existing tests passed unchanged after the refactor.
- *Honesty:* the clock is compressed 30x and the release history before each run is a fixed record; both are stated in the dataset's `meta`, in docs/REAL_TELEMETRY.md and in the UI header ("captured from a lab system, not production data"). Remediation actions stay simulated and allowlisted.
- *Rejected:* typing "realistic" numbers into more fixtures (that would still be invented); tuning the mock provider's regexes to the new log wording (it would flatter the mock, so its behaviour on this data is reported as it is); mixing simulated and captured incidents in one list (the mode is explicit and labelled).
- *Not done:* CloudWatch. It would be a third `TelemetrySource` and needs a read-only identity, redaction and bounded queries (listed in docs/REAL_TELEMETRY.md).

**D-031 · accepted · Real data exposed three agent-loop weaknesses; fixed in the tools and loop, not by tuning prompts.**
- *What failed:* on the captured incidents the live model produced 1 completed run out of its first 6 (see docs/EVALUATION.md, Section 4). Causes: no signal that the round budget was running out; `search_logs("error")` found nothing because the word is the level, not part of the message; and no valid way to cite "I searched and found nothing" (the model cited `none` and the whole answer was correctly rejected).
- *Decision:* last round forced to conclude (no tools, explicit notice; answers still fully validated); level names match in `search_logs`; an empty log search returns a citable absence id, mirroring deployments. Rejected: dropping ungrounded citations instead of failing (it would silently weaken the grounding rule), and more prompt wording (the prompt already forbade `none` and the model ignored it).
- *Result:* 9/9 live runs completed on captured data; the simulated live evaluation went from 16/18 to 18/18 completed with the injection still ignored 3/3 and the mock unchanged. A scorer note: citing an absence id is not citing a log line.
- *Still weak, reported:* ambiguous incidents still get medium instead of low confidence from the live model, and it sometimes does not search for the right word.


**D-032 · accepted · The first CI deploy failed because the OIDC trust used the name-based subject; trust GitHub's immutable subject.**
- *What happened:* the first production deploy (2026-10-30) passed all four check jobs and then failed at `configure-aws-credentials` with `Not authorized to perform sts:AssumeRoleWithWebIdentity`. The roles trusted `repo:SAURABHSALVE/agentops:...`, but this repository has immutable subject claims enabled (`gh api repos/SAURABHSALVE/agentops/actions/oidc/customization/sub` reports `sub_claim_prefix: repo:SAURABHSALVE@90780335/agentops@1437085899`), so GitHub signs the ID-bearing form and the strings never match.
- *Decision:* the OIDC stack takes the subject prefix as an input (`githubSubjectPrefix` in `cdk.json`); config validates that it names the same repository as `githubRepo`, with no wildcards. The default stays name-based for repositories that do not use immutable subjects. Rejected: turning off immutable subjects on the repository (it would make the existing roles match, but weakens the trust: a deleted or renamed repository name can be re-registered by someone else) and wildcard matching on the subject.
- *Cost:* this changes the trust policy of an account-level stack that is deliberately outside CI, so a human with an admin (non-root) identity must redeploy `AgentOps-GitHubOidc` once.
- *Process note:* 23 commits had never been through remote CI. Every CI step is now run locally in CI's order before pushing, and the first remote run goes through a pull request, not `main`, so a failure cannot trigger a production deploy.

**D-033 · accepted · AWS-dependent CI jobs wait for an explicit switch; real checks are never skipped.**
- *Why:* after D-032 the code is right, but the account's OIDC roles still carry the old trust until a human with an admin (non-root) identity redeploys `AgentOps-GitHubOidc`. Until then the PR `cdk diff` and the production deploy can only fail, which turns every pull request red and would make a merge to `main` attempt (and fail) a production deploy. The user asked for CI to be green now and the AWS deploy done later.
- *Decision:* both AWS jobs are gated on the repository variable `AWS_DEPLOY_ENABLED == 'true'`. When it is not set, a short job named "AWS jobs are off" prints a notice and a run summary, so a skipped AWS job cannot be mistaken for a passing one. Backend, frontend, infra (with `cdk synth --strict` for dev and prod) and shell checks are untouched and still run on every pull request and every push to `main`.
- *Rejected:* `continue-on-error` on the AWS step (it would hide real failures once AWS is on), deleting the diff job, and turning off immutable subjects on the repository (weakens the trust, see D-032).
- *Turning it on:* README, "Turning AWS deploys on". Branch protection is not available on this plan, so nothing here is enforced by GitHub rules; the variable is the switch.
