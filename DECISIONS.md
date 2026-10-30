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

**D-022 · OPEN · Remaining Phase 3/5/6 questions.**
(a) New fixtures are needed without altering INC-1001 to INC-1004 expectations: a prompt-injection log (3A) and a remediation-succeeds-but-verification-fails scenario (Phase 5). Decide whether these are new incidents (for example INC-1005/INC-1006) or variants of existing ones. (b) Public-demo access control for Phase 6 (allow-list or password, versus demoing locally only).

**D-023 · accepted (user, 2026-10-30) · AWS hosting: static site on S3 + CloudFront, API on Lambda + API Gateway HTTP API, investigation state in DynamoDB. Infrastructure as Python CDK in `infra/`.**
1. **Lambda over a container.** Idle cost is about zero and it matches a portfolio audience. The cost is that state cannot live in the container, so investigations move to DynamoDB (`DynamoDbInvestigationStore`, same `InvestigationStore` interface, selected by `STORAGE_MODE=dynamodb`). This pulls the cloud half of Phase 4 forward; local SQLite is still planned.
2. **Investigations run inside the POST request on Lambda.** Mangum returns the response only after Starlette background tasks finish, so the investigation completes within the invocation (the 202 body is still the `running` snapshot, and clients poll as before). That is fine for the deterministic mock (milliseconds) and bounded by the 29s Lambda timeout under API Gateway's 30s limit. **Phase 3B (live OpenAI) must revisit this** (asynchronous self-invoke, or a queue), because several model rounds can exceed 30s.
3. **Duplicate-start protection is a conditional write** (`LOCK#<incident>` item with a 60s expiry), so it holds across containers. A run whose container died is reported as `failed` on the next read rather than polling forever.
4. **Bedrock and AgentCore stay out** (D-018 reaffirmed). The only model-related AWS resource is an empty Secrets Manager slot for `OPENAI_API_KEY`, which nothing reads yet.
5. **New dependencies, vetted:** `mangum` 0.22.0 (MIT, Lambda adapter, imported only by `app/lambda_handler.py`), `boto3` 1.43.122 (Apache-2.0; Lambda supplies its own copy), `moto` 5.2.5 (Apache-2.0, dev only; its tests skip on Python 3.15 until PyYAML ships a wheel).
6. **Lambda runtime is Python 3.13**, with CI on the same version, so the deployed interpreter is the tested one.

**D-024 · accepted (user, 2026-10-30) · Frontend is a Next.js static export; Cache Components and partial prefetching are switched off. Amends D-011.**
`output: "export"` with `trailingSlash: true` produces plain files for S3 + CloudFront. Next.js 16.4.3 refuses the export while `cacheComponents` and `partialPrefetching` are on ("PPR cannot be enabled in export mode"). Nothing in `src/` used a caching API (they were `create-next-app` defaults), so they were removed. Consequences: unknown incident IDs now get a real 404 from the CDN instead of a streamed 200 (the D-011 caveat goes away); `generateStaticParams` still enumerates the four simulated incidents. If a route later needs request-time rendering, this decision must be revisited (Amplify Hosting or a server). `npm run typecheck` runs `next typegen` first because `PageProps`/`LayoutProps` are generated types, so a clean checkout cannot run bare `tsc`.
