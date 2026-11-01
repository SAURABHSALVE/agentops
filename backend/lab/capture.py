"""Run the lab through three real incidents and write the measured telemetry to a dataset file.

    cd backend
    .venv\\Scripts\\python -m lab.capture            # about 6 minutes; writes data/captured/lab-capture.json

Every log line and metric value comes from the lab (lab/shop.py) actually running with a fault injected.
Only two things are not measured, and the dataset says so in its `meta`: the clock is compressed (one real
second is 30 seconds of dataset time, so a 51-minute incident takes under two real minutes) and the release
history from before each run is a fixed record.
"""

import argparse
import json
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas import IncidentDetail, LogEntry, Signal, TimelineEvent
from lab import shop

TIME_SCALE = 30  # dataset seconds per real second
SECONDS_PER_MINUTE = 60 / TIME_SCALE
LEAD_MIN = 15  # healthy baseline before the fault
FAULT_MIN = 30  # fault active, investigation window
RECOVER_MIN = 6  # after the remediation, used for verification
TOTAL_MIN = LEAD_MIN + FAULT_MIN + RECOVER_MIN
WINDOW_END_BIN = LEAD_MIN + FAULT_MIN - 1
ALERT_RATIO = 3.0
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "captured" / "lab-capture.json"

Extractor = Callable[[shop.Recorder, float], list[float]]


# ---- turning raw lab events into one value per dataset minute --------------------------------------


def _bins(events: list[tuple[float, float]], t0: float) -> list[list[float]]:
    out: list[list[float]] = [[] for _ in range(TOTAL_MIN)]
    for at, value in events:
        k = int((at - t0) // SECONDS_PER_MINUTE)
        if 0 <= k < TOTAL_MIN:
            out[k].append(value)
    return out


def _carry(values: list[float | None]) -> list[float]:
    out: list[float] = []
    last = 0.0
    for v in values:
        last = last if v is None else v
        out.append(last)
    return out


def _percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, max(0, int(q * len(ordered) + 0.999999) - 1))]


def latency(service: str, q: float) -> Extractor:
    def extract(rec: shop.Recorder, t0: float) -> list[float]:
        bins = _bins([(at, ms) for at, ms, _ in rec.requests.get(service, [])], t0)
        return _carry([_percentile(b, q) if b else None for b in bins])

    return extract


def error_rate(service: str) -> Extractor:
    def extract(rec: shop.Recorder, t0: float) -> list[float]:
        bins = _bins([(at, 0.0 if ok else 1.0) for at, _, ok in rec.requests.get(service, [])], t0)
        return [100 * sum(b) / len(b) if b else 0.0 for b in bins]

    return extract


def query_p95(rec: shop.Recorder, t0: float) -> list[float]:
    return _carry([_percentile(b, 0.95) if b else None for b in _bins(rec.queries, t0)])


def queue_depth(rec: shop.Recorder, t0: float) -> list[float]:
    return _carry([max(b) if b else None for b in _bins(rec.depths, t0)])


# ---- scenarios -------------------------------------------------------------------------------------


@dataclass
class Rig:
    drivers: list[tuple[float, Callable[[int, float], None], int]]
    inject: Callable[[], str | None]  # returns the deploy summary if the fault is a release
    remediate: Callable[[], None]
    sampler: Callable[[], None] | None = None
    close: Callable[[], None] = lambda: None


@dataclass(frozen=True)
class Scenario:
    incident_id: str
    service: str
    title: str
    severity: str
    summary: str
    alert_metric: str
    alert_floor: float
    metrics: tuple[tuple[str, str, Extractor], ...]  # name, unit, extractor
    verify: tuple[str, ...]
    signals: tuple[tuple[str, str, str], ...]  # label, metric, unit suffix
    history: tuple[tuple[str, int, str, str], ...]  # deploy id, hours before the run, change type, summary
    deploy_id: str
    build: Callable[[shop.Recorder, random.Random], Rig]
    resolves: bool = True  # whether undoing the fault should bring the verified metrics back under their thresholds


FLOORS = {
    "error_rate": 1.0,
    "p99_latency_ms": 50.0,
    "p95_latency_ms": 20.0,
    "db_query_p95_ms": 20.0,
    "queue_depth": 25.0,
}


def _checkout(rec: shop.Recorder, rng: random.Random) -> Rig:
    payments = shop.Payments(rng)
    checkout = shop.Checkout(rec, payments)

    def inject() -> str | None:
        payments.stuck_fraction = 0.2
        return None

    def remediate() -> None:
        payments.stuck_fraction = 0.0

    return Rig([(40, checkout.handle, 96)], inject, remediate, close=payments.server.shutdown)


def _catalog(rec: shop.Recorder, rng: random.Random) -> Rig:
    catalog = shop.Catalog(rec, rng, rows=40_000)
    rec.log("catalog-api", "INFO", f"query plan for products_by_category: {catalog.query_plan()}")

    def inject() -> str | None:
        catalog.slow_query_deployed = True
        summary = "Make the product-listing category filter case-insensitive"
        rec.log("catalog-api", "INFO", f"release rolled out: {summary}")
        return summary

    def remediate() -> None:
        catalog.slow_query_deployed = False

    return Rig([(6, catalog.handle, 32)], inject, remediate)


def _worker(rec: shop.Recorder, rng: random.Random) -> Rig:
    notifier = shop.Notifier(rng)
    worker = shop.Worker(rec, notifier)
    worker.start()

    def inject() -> str | None:
        notifier.down = True
        return None

    # A restart does not touch the failing downstream, so the backlog is still there afterwards.
    return Rig(
        [(12, worker.enqueue, 4)], inject, worker.restart, sampler=worker.sample_depth, close=notifier.server.shutdown
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "INC-2001",
        "checkout-api",
        "Elevated HTTP 5xx on checkout",
        "critical",
        "Checkout requests are failing with 502/504 responses and slow responses; order completion has dropped.",
        "error_rate",
        FLOORS["error_rate"],
        (
            ("error_rate", "percent", error_rate("checkout-api")),
            ("p99_latency_ms", "ms", latency("checkout-api", 0.99)),
        ),
        ("error_rate", "p99_latency_ms"),
        (("5xx rate", "error_rate", "%"), ("p99 latency", "p99_latency_ms", " ms")),
        (("deploy-2301", 4, "code", "Update order-confirmation email template"),),
        "",
        _checkout,
    ),
    Scenario(
        "INC-2002",
        "catalog-api",
        "Catalog latency elevated",
        "high",
        "Product listing endpoints are slow; p95 latency rose sharply and has stayed high.",
        "p95_latency_ms",
        FLOORS["p95_latency_ms"],
        (
            ("db_query_p95_ms", "ms", query_p95),
            ("p95_latency_ms", "ms", latency("catalog-api", 0.95)),
            ("error_rate", "percent", error_rate("catalog-api")),
        ),
        ("db_query_p95_ms", "p95_latency_ms"),
        (("p95 latency", "p95_latency_ms", " ms"), ("Error rate", "error_rate", "%")),
        (("deploy-2300", 9, "dependency", "Bump http client library patch version"),),
        "deploy-2302",
        _catalog,
    ),
    Scenario(
        "INC-2003",
        "worker-service",
        "Worker queue backlog growing",
        "high",
        "Messages are accumulating faster than they are processed; the notification queue keeps growing.",
        "queue_depth",
        FLOORS["queue_depth"],
        (("queue_depth", "messages", queue_depth), ("error_rate", "percent", error_rate("worker-service"))),
        ("queue_depth",),
        (("Queue depth", "queue_depth", " messages"), ("Failed attempts", "error_rate", "%")),
        (("deploy-2303", 22, "config", "Increase worker batch size"),),
        "",
        _worker,
        resolves=False,
    ),
)


# ---- running one scenario ----------------------------------------------------------------------------


@dataclass
class Result:
    t0: float
    rec: shop.Recorder
    series: dict[str, list[float]]
    deploy_at: float | None = None
    deploy_summary: str | None = None
    notes: list[str] = field(default_factory=list)


def run(sc: Scenario, seed: int) -> Result:
    rng = random.Random(seed)
    rec = shop.Recorder()
    rig = sc.build(rec, rng)
    stop = threading.Event()
    threads = [
        threading.Thread(target=shop.drive, args=(rate, fn, stop, pool, random.Random(seed + i)), daemon=True)
        for i, (rate, fn, pool) in enumerate(rig.drivers)
    ]
    threads.append(threading.Thread(target=shop.housekeeping, args=(rec, sc.service, rng, stop, 12.0), daemon=True))
    if rig.sampler:
        sampler = rig.sampler

        def sample() -> None:
            while not stop.wait(0.25):
                sampler()

        threads.append(threading.Thread(target=sample, daemon=True))

    t0 = time.time()
    for t in threads:
        t.start()
    result = Result(t0, rec, {})

    def at(minute: int) -> None:
        time.sleep(max(0.0, t0 + minute * SECONDS_PER_MINUTE - time.time()))

    at(LEAD_MIN)
    deploy = rig.inject()
    if deploy:
        result.deploy_at, result.deploy_summary = time.time(), deploy
    at(LEAD_MIN + FAULT_MIN)
    rig.remediate()
    at(TOTAL_MIN)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    rig.close()
    result.series = {name: extract(rec, t0) for name, _, extract in sc.metrics}
    return result


# ---- assembling the dataset ------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def assemble(index: int, sc: Scenario, res: Result, log_counter: int) -> tuple[dict[str, Any], int]:
    base = datetime(2026, 10, 30, 9 + 3 * index, 0, tzinfo=UTC)
    minute = lambda k: base + timedelta(minutes=k)  # noqa: E731
    units = {name: unit for name, unit, _ in sc.metrics}

    alert = res.series[sc.alert_metric]
    baseline = sum(alert[:LEAD_MIN]) / LEAD_MIN
    limit = max(ALERT_RATIO * baseline, sc.alert_floor)
    started = next((k for k in range(LEAD_MIN, WINDOW_END_BIN) if alert[k] >= limit and alert[k + 1] >= limit), None)
    if started is None or WINDOW_END_BIN - started < 10:
        raise RuntimeError(
            f"{sc.incident_id}: no clear alert in the captured run (baseline {baseline:.2f}, limit {limit:.2f})"
        )

    def stat(metric: str) -> tuple[float, float]:
        s = res.series[metric]
        return sum(s[:LEAD_MIN]) / LEAD_MIN, max(s[started : WINDOW_END_BIN + 1])

    signals = []
    for label, metric, suffix in sc.signals:
        base_v, peak = stat(metric)
        signals.append(Signal(label=label, value=f"{peak:,.1f}{suffix} (baseline {base_v:,.1f}{suffix})"))
    incident = IncidentDetail(
        id=sc.incident_id,
        title=sc.title,
        service=sc.service,
        severity=sc.severity,  # type: ignore[arg-type]
        status="open",
        summary=sc.summary,
        started_at=minute(started),
        window_end=minute(WINDOW_END_BIN),
        updated_at=minute(WINDOW_END_BIN),
        signals=signals,
        history=[
            TimelineEvent(
                id=f"e-{sc.incident_id[4:]}-1",
                at=minute(started),
                kind="incident",
                title=f"Alert fired: {sc.alert_metric} above {limit:,.1f} for 2 min",
            )
        ],
    )

    logs: list[LogEntry] = []
    for line in sorted(res.rec.logs, key=lambda x: x.at):
        ts = base + timedelta(seconds=(line.at - res.t0) * TIME_SCALE)
        if base <= ts <= minute(WINDOW_END_BIN) + timedelta(seconds=59):
            log_counter += 1
            logs.append(
                LogEntry(
                    id=f"log-{log_counter}",
                    timestamp=ts,
                    level=line.level,  # type: ignore[arg-type]
                    service=line.service,
                    message=line.message,
                )
            )

    metrics: dict[str, Any] = {
        name: {
            "unit": units[name],
            "points": [[_iso(minute(k)), round(v, 2)] for k, v in enumerate(vals[: WINDOW_END_BIN + 1])],
        }
        for name, vals in res.series.items()
    }

    verification = []
    for metric in sc.verify:
        vals = res.series[metric]
        base_v = sum(vals[:LEAD_MIN]) / LEAD_MIN
        after = vals[LEAD_MIN + FAULT_MIN + 1 :]
        verification.append(
            {
                "metric": metric,
                "threshold": round(max(2 * base_v, FLOORS[metric]), 2),
                "post_remediation_value": round(sum(after) / len(after), 2),
            }
        )

    resolved = all(v["post_remediation_value"] < v["threshold"] for v in verification)
    if resolved != sc.resolves:
        raise RuntimeError(
            f"{sc.incident_id}: measured recovery {verification} contradicts the scenario (resolves={sc.resolves}). "
            "Machine load can distort a capture: close other programs and run it again."
        )

    deployments = [
        {
            "id": d_id,
            "service": sc.service,
            "deployed_at": _iso(base - timedelta(hours=hours)),
            "change_type": kind,
            "summary": text,
        }
        for d_id, hours, kind, text in sc.history
    ]
    if res.deploy_at is not None and res.deploy_summary:
        # Release systems record minute resolution, so the deploy is stamped with the minute it happened in.
        at = (base + timedelta(seconds=(res.deploy_at - res.t0) * TIME_SCALE)).replace(second=0, microsecond=0)
        deployments.append(
            {
                "id": sc.deploy_id,
                "service": sc.service,
                "deployed_at": _iso(at),
                "change_type": "code",
                "summary": res.deploy_summary,
            }
        )

    return (
        {
            "incident": incident.model_dump(mode="json"),
            "logs": [e.model_dump(mode="json") for e in logs],
            "metrics": metrics,
            "deployments": deployments,
            "verification": verification,
        },
        log_counter,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--only", help="run a single incident id (does not write the dataset)")
    parser.add_argument("--debug", action="store_true", help="print the post-remediation minutes")
    args = parser.parse_args()

    records: list[dict[str, Any]] = []
    counter = 2000
    for index, sc in enumerate(SCENARIOS):
        if args.only and sc.incident_id != args.only:
            continue
        print(f"[{sc.incident_id}] running {sc.service} for ~{TOTAL_MIN * SECONDS_PER_MINUTE:.0f}s ...", flush=True)
        res = run(sc, args.seed + index)
        if args.debug:
            for metric in sc.verify:
                after = res.series[metric][LEAD_MIN + FAULT_MIN :]
                print(f"    {metric} minutes from the remediation on: {[round(v, 1) for v in after]}")
        record, counter = assemble(index, sc, res, counter)
        records.append(record)
        inc = record["incident"]
        print(
            f"[{sc.incident_id}] alert at {inc['started_at']}, {len(record['logs'])} log lines, "
            f"verification {record['verification']}"
        )

    dataset = {
        "meta": {
            "kind": "lab-capture",
            "description": (
                "Telemetry measured from a small real system (backend/lab) with faults injected. Not production data."
            ),
            "captured_at": datetime.now(UTC).isoformat(),
            "time_scale": TIME_SCALE,
            "notes": [
                f"The clock is compressed {TIME_SCALE}x: {TOTAL_MIN} dataset minutes were measured in about "
                f"{TOTAL_MIN * SECONDS_PER_MINUTE:.0f} real seconds.",
                "Latencies are real milliseconds. Counts such as queue depth are the real value at that moment.",
                "Release history from before each run is a fixed record, not measured. "
                "A deploy during a run is a real event in the lab.",
                "Remediation outcomes (post_remediation_value) were measured after the lab actually undid the fault.",
            ],
        },
        "incidents": records,
    }
    if args.only:
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dataset, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
