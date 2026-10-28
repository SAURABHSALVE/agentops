# AgentOps AI

AI-assisted incident investigation over **simulated demo telemetry**. An LLM agent (OpenAI when enabled; a deterministic
mock by default) uses allowlisted, read-only diagnostic tools to propose an evidence-backed root cause; a human approves a
bounded, simulated remediation; the app verifies the result and keeps an audit timeline.

> **Status: Phases 1 and 2 approved; Phase 3A not started.** The `frontend/` UI still uses scripted in-browser
> mock data and does **not** call the `backend/` API yet. The API serves a deterministic simulator from memory.
> **No LLM is integrated yet** (not OpenAI, not any other provider), nothing is persisted, and nothing is deployed.
> Sections below describe intent unless stated otherwise. Architecture was revised on 2026-10-28 (see DECISIONS.md D-018).

## Intended run modes
1. **Default, no credentials:** frontend + backend + simulator + deterministic mock provider (works offline).
2. **LLM mode (planned, Phase 3B):** adds `AGENT_PROVIDER=openai` and `OPENAI_API_KEY` in the backend environment only.
3. **AWS mode (optional, later):** deployed backend; optionally DynamoDB and read-only CloudWatch telemetry.

Telemetry is **simulated** unless explicitly switched to real CloudWatch data, and the two are always labelled differently.

## Run the UI locally (Phase 1)
```
cd frontend
npm install
npm run dev      # http://localhost:3000
npm run lint && npx tsc --noEmit && npm run build
```
Requires Node 20+ (developed on Node 24).

## Run the API locally (Phase 2)
```
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt     # Windows; use .venv/bin/python elsewhere
.venv\Scripts\python -m uvicorn app.main:app --port 8000        # docs at http://localhost:8000/docs
.venv\Scripts\python -m pytest && .venv\Scripts\python -m ruff check . && .venv\Scripts\python -m mypy
```
The API serves **simulated** incidents, logs, metrics and deployments from memory (restart resets state).
The frontend does not call it yet. Developed on Python 3.14.

## Planned features
- Incident dashboard and detail view (simulated incidents, clearly labelled)
- Bounded, provider-agnostic tool-using investigator: OpenAI when enabled, deterministic labelled mock otherwise
- Evidence-backed findings with explicit uncertainty
- Approval-gated simulated remediation and verification
- Persisted investigation timeline

## Docs
[PLAN.md](PLAN.md) · [DECISIONS.md](DECISIONS.md) · [SECURITY.md](SECURITY.md) · build spec: `AgentOps_AI_Claude_Code_Build_Plan.md`

## Setup, architecture diagram, tests, evaluation results, deployment, cost and cleanup
To be written as the corresponding phases are completed and verified.
