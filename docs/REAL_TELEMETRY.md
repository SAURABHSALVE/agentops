# Real telemetry (captured from a lab system)

The default incidents (INC-1xxx) are hand-written fixtures. This mode serves incidents whose logs and metrics were
**measured from a real, running system** with faults injected. It is not production data, and the app says so in the
header. It needs no cloud account and no Docker.

## Use it
```
cd backend
.venv\Scripts\python -m uvicorn app.main:app --port 8000          # default: simulated incidents
set TELEMETRY_DATASET=data/captured/lab-capture.json              # then restart: captured incidents
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```
Or add `TELEMETRY_DATASET=data/captured/lab-capture.json` to `backend/.env`. The dashboard then lists INC-2001 to
INC-2003 and the header reads "Telemetry captured from a lab system". An unreadable or malformed dataset stops the
server at startup with a clear message instead of serving odd answers.

Mock provider or live model: the investigator works on both, but only the live model reasons about unfamiliar logs.
The mock is a rule table written against the simulated fixtures, so on captured data expect it to be less decisive.

## The lab (`backend/lab/`)
| Service | What really runs | Injected fault | Incident |
|---|---|---|---|
| `checkout-api` | Requests call `payments-gateway` over HTTP with a 250 ms timeout | 20% of payment calls hang | INC-2001: 5xx and slow responses |
| `catalog-api` | Real SQL against a real SQLite table (40k rows) | A "release" makes the category filter case-insensitive, so the index is no longer used | INC-2002: query and page latency jump |
| `worker-service` | A real queue, 4 consumers, 5 attempts with backoff against a `notifier` over HTTP | The notifier returns 503 | INC-2003: backlog grows; a worker restart does not help |

Alongside the fault, each service also logs routine unrelated work (cache warm-ups, config reloads) that it measured
itself, so the evidence is not only the failure lines.

`python -m lab.capture` (about 6 minutes) runs each scenario, with a healthy baseline, the fault, a remediation, and a
recovery period, and writes `backend/data/captured/lab-capture.json`. Alert time, signals, thresholds and
post-remediation values are computed from what was measured; nothing is typed in by hand except the alert's symptom
summary and the release history before each run.

## What is real and what is not
| Real (measured) | Not real |
|---|---|
| Every log line and its wording, produced by the lab code | The clock: compressed 30x (51 dataset minutes were measured in about 100 real seconds) |
| Latencies in real milliseconds, error rates, queue depth | Release history from before each run (fixed records) |
| Query plans and slow-query timings from SQLite | The remediation actions themselves (still simulated, still allowlisted) |
| Post-remediation values (measured after the lab undid the fault) | The scale: one process on a laptop, not production traffic |

The INC-2003 verification failure is genuine: restarting the consumers does not touch the failing notifier, so the
measured queue depth afterwards is still far above its threshold.

## Plugging in a different source later
The four diagnostic tools only talk to a `TelemetrySource` (`backend/app/simulator/source.py`): `incidents`, `logs`,
`metrics`, `deployments`, `verification`. `FixtureSource` and `CapturedSource` implement it. A CloudWatch source would be
a third implementation, with these requirements that this lab does not exercise:
- a **read-only** IAM role limited to specific log groups and metrics (never root or admin),
- **redaction** of emails, tokens and IDs before text reaches the model,
- bounded queries (time window and result caps, which the tools already enforce),
- the existing **untrusted-data** handling, which matters more because anyone can write a log line.

The agent loop, validation, allowlist and approval gate do not change with the source.

## Tests
`backend/tests/test_captured_source.py`: validated load (six malformed-file cases), windowing, no look-ahead past the
incident window, unique citable log ids, deployments, verification before and after the action, and that the agent
stays grounded and inside the allowlist on captured data.
