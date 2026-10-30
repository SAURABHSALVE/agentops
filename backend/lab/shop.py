"""A small, real, running system whose runtime telemetry is captured for the AgentOps demo.

Three services run in this process: `checkout-api` calls a `payments-gateway` over real HTTP,
`catalog-api` runs real SQL against a real SQLite database, and `worker-service` consumes a real queue
and calls a `notifier` over real HTTP. Faults are injected the way operators meet them (a degraded
dependency, a query that stops using its index, a downstream that returns 503), and every log line and
latency below is produced by that code actually running. Nothing here is production data.
"""

import http.client
import queue
import random
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PAYMENTS_TIMEOUT_S = 0.25
NOTIFIER_TIMEOUT_S = 0.5
MAX_ATTEMPTS = 5
SLOW_QUERY_MS = 20.0
CATEGORIES = tuple(f"cat-{n:02d}" for n in range(20))


@dataclass(frozen=True)
class LogLine:
    at: float
    service: str
    level: str
    message: str


class Recorder:
    """Thread-safe sink for everything the lab observes about itself."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.logs: list[LogLine] = []
        self.requests: dict[str, list[tuple[float, float, bool]]] = {}
        self.queries: list[tuple[float, float]] = []
        self.depths: list[tuple[float, float]] = []

    def log(self, service: str, level: str, message: str) -> None:
        with self._lock:
            self.logs.append(LogLine(time.time(), service, level, message))

    def request(self, service: str, latency_ms: float, ok: bool) -> None:
        with self._lock:
            self.requests.setdefault(service, []).append((time.time(), latency_ms, ok))

    def query(self, ms: float) -> None:
        with self._lock:
            self.queries.append((time.time(), ms))

    def depth(self, value: float) -> None:
        with self._lock:
            self.depths.append((time.time(), value))


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256  # the default of 5 drops connection bursts and adds seconds of fake latency

    def handle_error(self, request: Any, client_address: Any) -> None:  # clients that gave up are normal here
        pass


def _serve(respond: Callable[[], int]) -> _Server:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            code = respond()
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _answer
        do_POST = _answer

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = _Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _call(port: int, method: str, path: str, timeout: float) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


# ---- checkout-api and its dependency ---------------------------------------------------------------


class Payments:
    """The dependency. `stuck_fraction` is the share of requests that hang (a degraded upstream)."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.stuck_fraction = 0.0
        self.server = _serve(self._respond)

    def _respond(self) -> int:
        if self._rng.random() < self.stuck_fraction:
            time.sleep(1.0)
        else:
            time.sleep(self._rng.uniform(0.008, 0.025))
        return 200


class Checkout:
    service = "checkout-api"

    def __init__(self, rec: Recorder, payments: Payments) -> None:
        self._rec = rec
        self._port = payments.server.server_address[1]

    def handle(self, n: int, arrived: float) -> None:
        order = f"ord-{n:06d}"
        status = 200
        try:
            code = _call(self._port, "GET", "/charge", PAYMENTS_TIMEOUT_S)
            if code >= 500:
                status = 502
                self._rec.log(self.service, "ERROR", f"payments-gateway returned {code} (order_id={order})")
        except TimeoutError:
            status = 504
            self._rec.log(
                self.service,
                "ERROR",
                f"upstream timeout calling payments-gateway after {PAYMENTS_TIMEOUT_S * 1000:.0f}ms (order_id={order})",
            )
        except OSError as exc:
            status = 502
            self._rec.log(
                self.service, "ERROR", f"payments-gateway unreachable: {type(exc).__name__} (order_id={order})"
            )
        ms = (time.perf_counter() - arrived) * 1000
        self._rec.request(self.service, ms, status < 500)
        if status == 200 and n % 60 == 0:
            self._rec.log(self.service, "INFO", f"order completed in {ms:.0f}ms (order_id={order})")


# ---- catalog-api and its database ------------------------------------------------------------------

FAST_SQL = "SELECT id, name, price FROM products WHERE category = ? ORDER BY price LIMIT 20"
# What the "deploy" ships: a case-insensitive filter. Wrapping the column in lower() cannot use the index.
SLOW_SQL = "SELECT id, name, price FROM products WHERE lower(category) = ? ORDER BY price LIMIT 20"


class Catalog:
    service = "catalog-api"

    def __init__(self, rec: Recorder, rng: random.Random, rows: int) -> None:
        self._rec = rec
        self._dir = tempfile.TemporaryDirectory(prefix="agentops-lab-")
        self._db = str(Path(self._dir.name) / "catalog.sqlite3")
        conn = sqlite3.connect(self._db)
        conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, category TEXT, name TEXT, price REAL)")
        conn.executemany(
            "INSERT INTO products (category, name, price) VALUES (?, ?, ?)",
            ((rng.choice(CATEGORIES), f"item-{i}", round(rng.uniform(1, 500), 2)) for i in range(rows)),
        )
        conn.execute("CREATE INDEX idx_products_category_price ON products (category, price, name)")
        conn.commit()
        conn.close()
        self.slow_query_deployed = False
        self._plan_logged = False
        self._lock = threading.Lock()

    def query_plan(self) -> str:
        conn = sqlite3.connect(self._db)
        try:
            sql = SLOW_SQL if self.slow_query_deployed else FAST_SQL
            rows = conn.execute("EXPLAIN QUERY PLAN " + sql, ("cat-00",)).fetchall()
        finally:
            conn.close()
        return "; ".join(str(r[3]) for r in rows)

    def handle(self, n: int, arrived: float) -> None:
        request_id = f"req-{n:06d}"
        category = CATEGORIES[n % len(CATEGORIES)]
        conn = sqlite3.connect(self._db)
        try:
            started = time.perf_counter()
            conn.execute(SLOW_SQL if self.slow_query_deployed else FAST_SQL, (category,)).fetchall()
            query_ms = (time.perf_counter() - started) * 1000
        finally:
            conn.close()
        self._rec.query(query_ms)
        if query_ms > SLOW_QUERY_MS:
            self._rec.log(
                self.service,
                "WARN",
                f"query products_by_category took {query_ms:.0f}ms "
                f"(threshold {SLOW_QUERY_MS:.0f}ms, request_id={request_id})",
            )
            with self._lock:
                first = not self._plan_logged
                self._plan_logged = True
            if first:
                self._rec.log(self.service, "WARN", f"query plan for products_by_category: {self.query_plan()}")
        ms = (time.perf_counter() - arrived) * 1000
        self._rec.request(self.service, ms, True)
        if n % 60 == 0:
            self._rec.log(self.service, "INFO", f"listing served in {ms:.0f}ms (request_id={request_id})")


# ---- worker-service and its downstream -------------------------------------------------------------


class Notifier:
    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.down = False
        self.server = _serve(self._respond)

    def _respond(self) -> int:
        time.sleep(self._rng.uniform(0.08, 0.15))
        return 503 if self.down else 200


class Worker:
    service = "worker-service"
    consumers = 4

    def __init__(self, rec: Recorder, notifier: Notifier) -> None:
        self._rec = rec
        self._port = notifier.server.server_address[1]
        self._queue: queue.Queue[str] = queue.Queue()
        self._generation = 0
        self._warned: set[int] = set()
        self._dead_letters = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        generation = self._generation
        for index in range(self.consumers):
            threading.Thread(target=self._consume, args=(generation, index), daemon=True).start()

    def restart(self) -> None:
        self._generation += 1
        self._rec.log(self.service, "INFO", f"worker restarted: {self.consumers} consumers started")
        self.start()

    def enqueue(self, n: int, arrived: float) -> None:
        self._queue.put(f"msg-{n:06d}")

    def sample_depth(self) -> None:
        depth = self._queue.qsize()
        self._rec.depth(depth)
        for mark in (100, 250, 500, 1000):
            with self._lock:
                crossed = depth >= mark and mark not in self._warned
                if crossed:
                    self._warned.add(mark)
            if crossed:
                self._rec.log(self.service, "WARN", f"queue depth above {mark} messages")

    def _consume(self, generation: int, index: int) -> None:
        while self._generation == generation:
            try:
                message = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._process(message)

    def _process(self, message_id: str) -> None:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                code = _call(self._port, "POST", "/notify", NOTIFIER_TIMEOUT_S)
            except OSError:
                code = 599
            ok = code == 200
            self._rec.request(self.service, (time.perf_counter() - started) * 1000, ok)
            if ok:
                return
            self._rec.log(
                self.service,
                "ERROR",
                f"notifier responded {code} (attempt {attempt}/{MAX_ATTEMPTS}, message_id={message_id})",
            )
            time.sleep(0.15 * attempt)
        with self._lock:
            self._dead_letters += 1
            dead = self._dead_letters
        self._rec.log(
            self.service,
            "ERROR",
            f"message {message_id} moved to dead-letter queue after {MAX_ATTEMPTS} attempts (dlq size {dead})",
        )


# ---- background behaviour every service has ---------------------------------------------------------


def housekeeping(rec: Recorder, service: str, rng: random.Random, stop: threading.Event, every: float) -> None:
    """Routine, unrelated work that logs what it measured (cache warm-ups and config reloads)."""
    while not stop.wait(rng.uniform(every * 0.5, every * 1.5)):
        started = time.perf_counter()
        if rng.random() < 0.5:
            cache = {i: str(i) * 3 for i in range(rng.randint(20_000, 60_000))}
            ms = (time.perf_counter() - started) * 1000
            rec.log(service, "INFO", f"cache warm-up completed in {ms:.0f}ms ({len(cache)} entries)")
        else:
            time.sleep(rng.uniform(0.01, 0.05))
            ms = (time.perf_counter() - started) * 1000
            rec.log(service, "INFO", f"config reload took {ms:.0f}ms")


def drive(rate: float, fn: Callable[[int, float], None], stop: threading.Event, pool: int, rng: random.Random) -> None:
    """Open-loop load: requests arrive at `rate` per second whether or not the service keeps up."""
    executor = ThreadPoolExecutor(max_workers=pool)
    n = 0
    due = time.perf_counter()
    while not stop.is_set():
        due += rng.expovariate(rate)
        wait = due - time.perf_counter()
        if wait > 0 and stop.wait(wait):
            break
        executor.submit(fn, n, time.perf_counter())
        n += 1
    executor.shutdown(wait=False, cancel_futures=True)
