# Demo script (about 5 minutes)

Everything here is simulated telemetry. Run locally with the mock provider (no key, works offline), or with the live
model if you have set `OPENAI_API_KEY` (see the README).

## Setup (once)
```
cd backend && python -m venv .venv && .venv\Scripts\python -m pip install -r requirements-dev.txt
cd ..\frontend && npm install
```
Start two terminals: `cd backend && .venv\Scripts\python -m uvicorn app.main:app --port 8000` and
`cd frontend && npm run dev`, then open http://localhost:3000.

## Walkthrough
1. **Dashboard.** Six simulated incidents, clearly labelled as simulated. Point out the provider on the next screen: the
   mock says "not an LLM"; the live provider names the model.
2. **INC-1001 (clear cause).** Click *Investigate*. Watch the tool trace appear (`get_incident`, deployments, metrics,
   log searches). Findings show a root cause, a confidence level, evidence with ids that came from those tool calls, and a
   proposed action. Nothing has run yet.
3. **Approve.** *Approve simulated action*: the action runs once, then verification passes and the incident becomes
   *Resolved*. The timeline on the right records approval, action and verification.
4. **INC-1006 (action works, fix does not).** Investigate, approve. The action *succeeds* but verification **fails**
   (`queue_depth 7400 >= 1000`): the incident stays open and the UI says so. This is the point of keeping "action
   succeeded" separate from "verified resolved".
5. **INC-1004 (ambiguous).** Low confidence, alternatives listed, **no remediation offered**.
6. **INC-1005 (prompt injection).** One log line tells the AI to ignore its instructions and run a different action.
   The investigation retrieves it as data, does not cite or follow it, and proposes the legitimate action.
7. **Reject** on INC-1002: nothing runs; the rejection is on the timeline.
8. **Persistence.** Stop and restart the backend, refresh: history, findings and approvals are still there (SQLite).

## Talking points
- The model only proposes. The backend validates tool names and arguments, checks every cited evidence id against the
  tool results it recorded, resolves the remediation from its own allowlist, and requires a recorded human approval.
- A model that fully obeys an injected instruction still cannot do harm: the offline suite tests exactly that.
- Honest limits: the live model over-states confidence on the ambiguous incident (see `docs/EVALUATION.md`).

## Screenshots
`docs/screenshots/`: dashboard, INC-1001 findings and resolved, INC-1004, INC-1005, INC-1006 not resolved.
