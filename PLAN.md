# AgentOps AI — Plan

**Revised 2026-10-28 for D-018 (OpenAI as model provider, local-first; Bedrock/AgentCore removed from the MVP).**
Where `AgentOps_AI_Claude_Code_Build_Plan.md` mentions Bedrock or AgentCore, this file and DECISIONS.md take precedence.

## Phase status (only explicit user approvals count)
| Phase | State |
|---|---|
| 0 Recon and plan | Done. Proceeded on the user's "yes start with phase 1"; the literal phrase `APPROVE PHASE 0` was not used |
| 1 UI skeleton (mock data) | **Approved** by the user ("approve start phase 2") |
| 2 FastAPI + deterministic simulator | **Approved** by the user (`APPROVE PHASE 2`, 2026-10-28). Verified locally: 84 tests, ruff, mypy |
| 3A Offline investigator + API/UI integration | **Built and tested** (user asked for phases through 5, 2026-10-30): mock provider, bounded coordinator, evidence grounding, API, UI wired to it |
| 3B Live OpenAI integration | **Built and tested** against the live API with `gpt-4.1-mini`; see DECISIONS D-026 and the measured results in backend/evaluation |
| 6 (infrastructure and CI/CD part) | **Pulled forward at the user's request (2026-10-30).** CDK stacks, GitHub Actions with OIDC, alarms, budget, teardown: **deployed 2026-11-01** (Deploy #7, smoke-tested). Platform decision recorded as D-023/D-024. Live OpenAI hosting and public-demo abuse controls remain Phase 6/3B work |
| 4 Persistence and timeline | **Built and tested**: SQLite store, restart recovery, derived timeline, duplicate-start protection |
| 5 Approval, remediation, verification | **Built and tested**: server-side gate, runs once, verification is a separate state, INC-1006 verification failure |
| 6 Deployment and operations | **Done and live** (mock provider, simulated data). Access code, rate limits, secrets, opt-in live mode, CDK, OIDC CI/CD. The OpenAI provider is not enabled on AWS yet |
| 7 Evaluation and hardening | **Done**: seven-case offline suite, measured mock and live runs, weaknesses reported (docs/EVALUATION.md) |
| 8 Portfolio finish | **Done** except live-site screenshots: README, diagram, demo script, screenshots, cleanup, measured résumé bullets |
| 13 First-run experience and "my work" | **Built and tested** (branch `feat/phase-13-15-user-value`): My investigations page, delete my investigations, first-run welcome, sign-up guidance, "none yet" is a 200 |
| 14 Safety before inviting people | **Built and tested**: per-user caps, Content-Security-Policy (verified in a browser against the production build), refused-token alarm. Sign-up policy (open, invite, domain allow-list) is left open for the owner to decide |
| 18 Better live answers | **Built and tested locally** (branch `feat/richer-live-answers`, D-045): a fifth tool reads the alarm's neighbouring metrics (a function's invocations, duration and throttles next to its errors alarm), and an answer that says "no deployments" while the tool returned some is sent back once, then flagged and capped at medium. Not yet run with a real model on a real account. |
| 17 Many users | **Built and tested locally** (branch `feat/phase-17-scale-to-many-users`): any account may connect (`connectAccounts=any`), the access code is a one-time invitation, daily investigation caps per user and per site (DynamoDB), a wider live window, CloudTrail filtered by service, one repair retry for ungrounded answers, API throttle 25/50. **Not done**: a load test, a manual IAM deploy of these settings, an invite-code UI beyond the existing access-code box |
| 16 Connect a real AWS account | **16A and 16B built and tested locally** (branches `feat/phase-16a-aws-connection`, `feat/phase-16b-live-read`): connection, consent, read-only role template, then assuming the role and reading alarms, the alarm's metric, listed log groups and CloudTrail changes as incidents, through the same four tools; per user, off by default (CDK `-c awsConnect=on`). Verified against stubs only (botocore Stubber, fake readers, moto). **Not done**: 16D, a real test in the owner's account, which needs approval for each AWS change; the IAM change in 16B is unapplied (a manual deploy with the IAM box ticked). Nothing has read a real account yet |
| 15 A more useful AI | **Built and measured**: a backend confidence ceiling (live: 18/18 reported within cap; the guard acted 1 time in 18, see docs/EVALUATION.md Section 7) and a one-variable switch for live mode. **Live mode itself is not turned on**: it needs the owner's own key and spend limit |
| 12 Sign-in and per-user data | **Deployed 2026-11-02** (PR #9): Cognito user pool, PKCE sign-in, backend token verification, records per user in all three stores, 31-check browser test against a fake Cognito. Live: writes refused without sign-in, hosted page accepts the client and callback, Lambda reaches Cognito. A full human sign-up/sign-in is not yet checked |

Current reality: the UI is wired to the API. The mock provider is the default; the OpenAI provider is opt-in. Local storage
is a SQLite file. The site is also deployed on AWS (see README); DynamoDB replaces SQLite there.

## What this is
A portfolio MVP: pick a *simulated* production incident, let an LLM agent investigate it with allowlisted read-only
tools, get an evidence-backed hypothesis (or an honest "insufficient evidence"), approve a bounded *simulated*
remediation, verify, and keep an audit timeline.

**Simulated vs real telemetry.** All incidents, logs, metrics and deployments come from the deterministic simulator and
are labelled "simulated demo telemetry" in the UI and README. CloudWatch is an *optional* later integration for real
operational telemetry; it is never the default, and real and simulated data must never be presented as the same thing.

## Architecture (revised)

```
Browser ─▶ Next.js (TS + Tailwind) ─▶ FastAPI (Pydantic v2)
                                        ├─ Investigation coordinator (bounded loop; provider-agnostic)
                                        │     ├─ LLMProvider ──┬─ MockProvider   (default; deterministic; no network; no credentials)
                                        │     │                └─ OpenAIProvider (opt-in; OPENAI_API_KEY server-side only)
                                        │     └─ Tool registry (allowlist + strict arg models + timeouts + size caps)
                                        │           get_incident · search_logs · get_service_metrics · get_recent_deployments
                                        ├─ Telemetry: Simulator (default)  |  CloudWatch (optional, later, read-only)
                                        ├─ Approval gate (server-side state machine) ─▶ allowlisted simulated remediation ─▶ verify
                                        └─ Repository interface ─▶ in-memory (today) | SQLite (Phase 4) | DynamoDB (optional)
```

The model only *proposes* tool calls and a final answer. The backend validates tool names and arguments, executes tools,
records the actual results, checks that every cited evidence ID came from those results, validates the final answer
against a Pydantic schema, and enforces human approval. No shell, SQL, IAM or infrastructure access from the model.

### Provider abstraction (kept deliberately small)
One interface (`LLMProvider`, a single `complete(...)` call taking messages, tool schemas and limits and returning either
tool calls or a final answer plus token usage) with two implementations. No framework. The coordinator never imports an
SDK. Tests use a third, test-only `ScriptedProvider` that returns crafted (including malicious or malformed) responses.

- **MockProvider** is a rule-based policy over tool results, not a canned answer table, and never reads the answer key.
  It must be labelled "mock / deterministic, not an LLM" in the API response and the UI.
- **OpenAIProvider** is built in Phase 3 behind `AGENT_PROVIDER=openai`. Use the current official API and SDK; before
  coding, read their docs and license, and do not guess API shapes or model IDs. The model name comes from `OPENAI_MODEL`
  only. Use structured outputs / function calling where the chosen model supports them, and **always** re-validate with
  Pydantic server-side regardless of provider claims.

### Bounds on every model-driven run
Max rounds (`AGENT_MAX_ROUNDS`), per-request timeout, max output tokens per call, **max total tokens per investigation**
(abort with an honest "budget exceeded" status), at most one retry and only for transient provider errors (tool calls are
read-only, so retrying is safe; never retry validation failures), tool result size caps, per-tool timeouts. Log latency
and token counts, never prompt or response content.

## Stack decisions at a glance
| Concern | Choice | Fallback / note |
|---|---|---|
| UI | Next.js + TypeScript + Tailwind (built, Phase 1) | — |
| API | FastAPI + Pydantic v2 (built, Phase 2) | — |
| Model provider | OpenAI API (opt-in), model in `OPENAI_MODEL` | `MockProvider` default; works with no credentials |
| Storage | Repository interface; in-memory today | SQLite via the standard library in Phase 4 (decided, D-021); DynamoDB optional |
| Orchestration | Plain backend state machine | Step Functions out of scope |
| Bedrock / AgentCore | **Removed from the MVP.** Not installed, configured or invoked | Only reconsidered on explicit user approval |
| Deploy | AWS is the intended target; platform chosen in Phase 6 after checking permissions, cost, feasibility | Documented local run is the guaranteed demo path |
| Real telemetry | CloudWatch optional, read-only, if time and permissions allow | Simulator stays default |
| Out of scope | S3, Step Functions, Kubernetes, multi-agent | Only with a concrete requirement and spare time |

## Local-first run modes (target for the finished MVP)
1. **Default, no credentials:** frontend + backend + simulator + `MockProvider`. Everything works offline.
2. **LLM mode:** same, plus `AGENT_PROVIDER=openai` and `OPENAI_API_KEY` in the backend environment only.
3. **AWS mode (optional, later):** deployed backend; optionally DynamoDB and/or CloudWatch telemetry, using a least-privilege IAM identity.

Modes 1 and 2 must never require AWS credentials. Only Phase 6 and the optional DynamoDB/CloudWatch pieces touch AWS.

## Phases and acceptance criteria (revised; each stops for explicit approval)

**Phase 3 — LLM investigator** *(split into 3A and 3B, decided in D-021; each has its own review gate)*
- **3A, fully offline, no external credentials:** `LLMProvider` + `MockProvider` + `ScriptedProvider` (tests); bounded
  coordinator; investigation API endpoints; evidence-ID grounding check; Pydantic validation of the final answer; tool-call
  record (name, redacted args, duration, status, result summary); UI wired to the API and showing the real tool trace;
  the stale Bedrock sentence in `frontend/src/app/page.tsx` corrected as part of this frontend change.
- **3B, needs your OpenAI key in `backend/.env` (git-ignored):** `OpenAIProvider`; one live end-to-end run; usage and
  latency logged; clear fallback on provider error, timeout, malformed output or exhausted token budget. A conventional
  `.env` loader (for example `python-dotenv`) is added only if the shell environment proves insufficient, and is vetted first.
- Tests: clear root cause; ambiguous evidence yields uncertainty, not an invented cause; tool failures (timeout,
  unavailable metric, unknown incident); malformed model output (invalid JSON, unknown tool, extra fields, evidence ID not
  in tool results, remediation not on the allowlist); prompt injection from log text (needs a new fixture, below).
- Acceptance: all of the above pass with `MockProvider`/`ScriptedProvider` and no network; the key never appears in
  logs, responses, the repo or the browser bundle.

**Phase 4 — Persistence and timeline.** Persist incidents, investigations and an append-only event history in a local
SQLite database (Python standard library, no new dependency; decided in D-021) behind the existing repository interface; polling in the UI; duplicate-start and duplicate-remediation protection.
Evaluate DynamoDB afterwards as an optional backend, adopted only if setup is straightforward and it materially improves the
demo. Acceptance: history survives refresh and backend restart in local mode; the UI states the real storage mode.

**Phase 5 — Approval, remediation, verification.** Server-side state machine; approve/reject endpoints; remediation
unreachable without recorded approval; duplicate approvals run once; "action succeeded" and "verified resolved" are
separate states. Tests include approval enforcement and **remediation verification failure** (needs a new fixture where an
action succeeds but verification fails).

**Phase 6 — AWS deployment and operations.** Prerequisite: a least-privilege IAM identity (see Security). Choose the hosting
platform here by checking account permissions, region availability, cost and credit coverage, then record it as a decision.
Store `OPENAI_API_KEY` in a managed secret store, never in the image, repo or frontend. Protect the public demo against
abuse of a billable key (access control and rate limit on the investigate endpoint, a spend limit on the OpenAI project).
CloudWatch for the app's own logs; optional CloudWatch *telemetry* integration only if time permits. Budget alerts, smoke
test, documented cleanup. Fallback: documented local run.

**Phase 7 — Evaluation and hardening.** Repeatable evaluation of at least these cases: clear root cause, ambiguous evidence,
unrelated logs, tool failure, malformed model output, prompt injection, remediation verification failure. Assertions for
evidence grounding, expected root-cause category, safe uncertainty, allowlist and approval enforcement. Report only measured
results; if a live OpenAI run is included, report its real latency and token usage and state the model used.

**Phase 8 — Portfolio finish.** README, architecture diagram matching the implementation, demo script, screenshots,
cleanup instructions, résumé bullet that describes only what was built and measured.

### New fixtures required (do not alter INC-1001 to INC-1004 expectations)
- A log line containing an injection attempt (Phase 3), as a new incident or variant, decided then.
- A scenario where a remediation executes but verification fails (Phase 5).

## Scope
**Required MVP:** dashboard, simulated incidents including an ambiguous one, 4 read-only diagnostic tools, bounded
provider-agnostic agent loop with a no-credentials mock mode, validated evidence-grounded findings, approval-gated
simulated remediation and verification, persisted timeline, evaluation suite, README/demo.
**Optional (only if time remains):** OpenAI live mode polish, DynamoDB backend, CloudWatch read-only telemetry, SSE streaming.
**Non-goals:** real infrastructure access or mutation, RBAC/multi-tenant, training, multi-agent swarms, real customer data,
Bedrock, AgentCore, Step Functions, S3, Kubernetes.

## Phase 0 findings (verified 2026-10-28; historical)
| Item | Result |
|---|---|
| Workspace | `D:\AWS\agentops` contained only the build plan; Git repo initialised afterwards (no commits) |
| Node / npm | v24.22.1 / 11.19.1 installed at `C:\Program Files\nodejs` but **not on PATH** in the shell |
| Python | 3.14.10 (only version); `pydantic-core` cp314 wheel exists; verified by a clean-room install in Phase 2 |
| AWS CLI | 2.39.0; profile `default`; region `us-east-1` |
| AWS identity | Account ID omitted from this document; caller is the **root user**; root MFA enabled; 0 IAM users. Whether root has long-lived access keys is **unverified** |
| Bedrock | Out of scope (D-018). Models were *listed* in us-east-1 during Phase 0 but no model was ever invoked |
| OpenAI | `OPENAI_API_KEY` is not set in this shell (presence checked, value never read). You report having a key; supplying it is yours to do |
| Credits / billing | Not verified. OpenAI usage is billed by OpenAI, so AWS credits should **not** be assumed to cover it |
| REA | `rea-agents` 6.8.0 exists on npm; **not installed** (D-009 open, D-013) |

## Blockers and open items
1. **AWS credentials are deferred, not blocking.** Phases 3 to 5 run locally. A least-privilege IAM identity is required
   before any AWS operation (Phase 6, optional DynamoDB/CloudWatch). See SECURITY.md.
2. **OpenAI key and spend limit** are needed for 3B only. You provision the key in your own environment; set a hard spend limit on its project.
3. REA installation scope (D-009), still open and not needed so far.
4. Git identity (`user.name`/`user.email`) is unset, so no commit can be made yet; no commit has been requested.
5. Node PATH: new terminals may need `C:\Program Files\nodejs` added (per-command workaround in use).
6. UI copy in `frontend/src/app/page.tsx` still says "real Bedrock-driven tool calls". Decided (D-021): correct it in the next relevant frontend change, which is 3A.

## Decided (D-021) and still open (D-022)
Decided: Phase 3 split into 3A/3B; local secrets in a git-ignored `backend/.env` (loader only if needed); SQLite via the
standard library for Phase 4; stale UI copy fixed in 3A; Bedrock, AgentCore and AWS resource creation out of scope until
explicitly approved, with AWS deployment evaluated in Phase 6 using least-privilege credentials.

Still open (D-022):
1. Injection and verification-failure scenarios: new incidents (for example INC-1005/INC-1006) or variants of existing ones? Needed before 3A (injection) and Phase 5 (verification failure).
2. Public demo access control for Phase 6: password/allow-list, or run only locally for the demo?

## Risks and fallbacks
| Risk | Impact | Mitigation / fallback |
|---|---|---|
| OpenAI cost or quota | Unexpected spend; rate limits | Mock mode is default; token and round budgets; project spend limit; small model for dev |
| Deployed key abuse | Anyone can burn tokens via a public endpoint | Access control, rate limit and spend cap before exposing publicly; otherwise demo locally |
| Data egress | Logs and prompts leave AWS for a third party | Only synthetic data in the MVP; real CloudWatch data must be opt-in and redacted, never default |
| API/SDK drift | Tool-calling shapes and model IDs change | Read current docs in Phase 3B; model in env; contract tests via `ScriptedProvider` |
| Vendor outage / latency | Live demo fails | Mock provider fallback, clearly labelled; recorded trace in README |
| Prompt injection via logs | Model follows instructions in log text | Logs delimited as data; allowlist; server-side approval; injection tests (Phase 3, 7) |
| Model invents evidence | Ungrounded findings | Evidence IDs checked against recorded tool results; ungrounded output rejected |
| Less "AWS-native" portfolio story | Weaker AWS signal | Real AWS value in deployment, secrets, logging, IAM; stated honestly, no overclaiming |
| Root credentials | Over-privileged account | Never used for dev, deploy or app code; IAM prerequisite before AWS work |
| DynamoDB / CloudWatch effort | Schedule | Optional; local store and simulator remain defaults |
| Deployment platform unknown | Time | Chosen in Phase 6 after checks; local run documented as fallback |
