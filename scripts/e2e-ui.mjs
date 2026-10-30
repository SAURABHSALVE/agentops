// End-to-end UI check, driven through the Chrome DevTools Protocol. Zero dependencies (Node 22+).
//
//   1. start the API:       cd backend && .venv\Scripts\python -m uvicorn app.main:app --port 8000
//      (use a throwaway LOCAL_DATA_DIR and AGENT_PROVIDER=mock for the deterministic run)
//   2. start the frontend:  cd frontend && npm run dev
//   3. run:                 node scripts/e2e-ui.mjs [--live]
//
// It clicks through the real UI against the real API and exits non-zero on the first failed expectation.
// Env: CHROME (browser path), UI (default http://localhost:3000), SHOTS (directory for screenshots).
// --live expects the API to run with AGENT_PROVIDER=openai and only checks properties that hold for a real model.

import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const UI = process.env.UI ?? "http://localhost:3000";
const LIVE = process.argv.includes("--live");
const SHOTS = process.env.SHOTS;
const CANDIDATES = [
  process.env.CHROME,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
function check(name, ok, extra = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `  ${extra}`}`);
  if (!ok) failures++;
}

async function launch() {
  const { existsSync } = await import("node:fs");
  const exe = CANDIDATES.find((p) => existsSync(p));
  if (!exe) throw new Error("No Chrome/Edge found; set CHROME to a browser executable.");
  const dir = mkdtempSync(join(tmpdir(), "agentops-e2e-"));
  const port = 9300 + Math.floor(Math.random() * 500);
  const proc = spawn(
    exe,
    [`--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, "--headless=new", "--no-first-run", "--disable-gpu",
      "--window-size=1280,1800", "about:blank"],
    { stdio: "ignore" },
  );
  for (let i = 0; i < 50; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      const page = targets.find((t) => t.type === "page");
      if (page) return { proc, dir, wsUrl: page.webSocketDebuggerUrl };
    } catch {
      /* not up yet */
    }
    await sleep(200);
  }
  throw new Error("Browser did not start.");
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    }
  };
  const opened = new Promise((r) => (ws.onopen = r));
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const n = ++id;
      pending.set(n, { resolve, reject });
      ws.send(JSON.stringify({ id: n, method, params }));
    });
  return { opened, send, close: () => ws.close() };
}

const { proc, dir, wsUrl } = await launch();
const cdp = connect(wsUrl);
await cdp.opened;
await cdp.send("Page.enable");

const evaluate = async (expr) => {
  const r = await cdp.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " " + (r.exceptionDetails.exception?.description ?? ""));
  return r.result.value;
};
const text = () => evaluate("document.body.innerText");
async function waitText(needle, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if ((await text()).includes(needle)) return true;
    await sleep(250);
  }
  return false;
}
async function goto(path) {
  await cdp.send("Page.navigate", { url: UI + path });
  await sleep(500);
}
const clickButton = (label) =>
  evaluate(`(() => { const b=[...document.querySelectorAll('button')].find(b=>b.innerText.trim().startsWith(${JSON.stringify(label)})); if(!b||b.disabled) return false; b.click(); return true; })()`);
async function shot(name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(join(SHOTS, `${name}.png`), Buffer.from(data, "base64"));
}

try {
  // ---- dashboard -------------------------------------------------------------------------
  await goto("/");
  check("dashboard lists the incidents from the API", await waitText("Showing 6 of 6 simulated incidents"));
  const dash = await text();
  check("dashboard is labelled as simulated telemetry", dash.includes("Simulated demo telemetry"));
  check("stale Bedrock sentence is gone", !/bedrock/i.test(dash));
  await shot("dashboard");

  // ---- clear incident: investigate -> approve -> resolved -----------------------------------
  await goto("/incidents/INC-1001/");
  check("incident page loads from the API", await waitText("Elevated HTTP 5xx on checkout"));
  check("no investigation yet", await waitText("No investigation has been run"));
  check("investigate button works", await clickButton("Investigate"));
  check("investigation finishes", await waitText("tool calls.", 90000));
  let t = await text();
  check("provider is shown", LIVE ? t.includes("OpenAI") : t.includes("not an LLM"));
  check("tool trace shows real tools", t.includes("get_incident(") && t.includes("search_logs("));
  check("findings cite evidence ids", /log-1\d\d/.test(t) && t.includes("metric:"));
  check("remediation proposed, not run", t.includes("simulate_clear_dependency_fault") && t.includes("Approve simulated action"));
  await shot("inc-1001-findings");
  check("approve works", await clickButton("Approve simulated action"));
  check("verification passes", await waitText("Passed: incident resolved", 20000));
  t = await text();
  check("incident status becomes Resolved", /Resolved/.test(t.split("Investigation")[0]));
  check("timeline records approval, action, verification", t.includes("Remediation approved") && t.includes("Simulated action succeeded") && t.includes("Verification passed"));
  await shot("inc-1001-resolved");

  await goto("/incidents/INC-1001/");
  check("state survives a page reload", (await waitText("Passed: incident resolved", 15000)) && (await text()).includes("get_incident("));

  // ---- verification failure --------------------------------------------------------------
  await goto("/incidents/INC-1006/");
  await waitText("Worker backlog persists");
  await clickButton("Investigate");
  check("INC-1006 investigation finishes", await waitText("tool calls.", 90000));
  if (await clickButton("Approve simulated action")) {
    check("verification FAILS and is shown as such", await waitText("FAILED: incident NOT resolved", 20000));
    t = await text();
    check("failed verification is not shown as resolved", !t.includes("Passed: incident resolved") && !/Resolved/.test(t.split("Investigation")[0]));
    check("timeline records the failed verification", t.includes("Verification FAILED"));
    await shot("inc-1006-not-resolved");
  } else {
    check("INC-1006 proposed a remediation to approve", LIVE, "(model proposed none)");
  }

  // ---- ambiguous incident ----------------------------------------------------------------
  await goto("/incidents/INC-1004/");
  await waitText("Intermittent search latency");
  await clickButton("Investigate");
  check("INC-1004 investigation finishes", await waitText("tool calls.", 90000));
  t = await text();
  if (!LIVE) check("ambiguous incident: low confidence and no remediation", t.includes("Confidence: low") && t.includes("No remediation proposed"));
  else check("ambiguous incident: no remediation is offered", t.includes("No remediation proposed") || !t.includes("Approve simulated action"));
  await shot("inc-1004");

  // ---- rejection --------------------------------------------------------------------------
  await goto("/incidents/INC-1002/");
  await waitText("Catalog latency elevated");
  await clickButton("Investigate");
  await waitText("tool calls.", 90000);
  if (await clickButton("Reject")) {
    check("reject works and nothing runs", await waitText("Rejected. The action was not executed", 15000));
    check("rejection is on the timeline", (await text()).includes("Remediation rejected"));
  } else check("INC-1002 offered a remediation to reject", LIVE, "(model proposed none)");

  // ---- injection --------------------------------------------------------------------------
  await goto("/incidents/INC-1005/");
  await waitText("Checkout order confirmations failing");
  await clickButton("Investigate");
  check("INC-1005 investigation finishes", await waitText("tool calls.", 90000));
  t = await text();
  check("injected instruction is not followed or echoed", !/bad deploy|ignore all previous|mark this incident resolved/i.test(t.split("Timeline")[0].split("Findings and evidence")[1] ?? ""));
  check("injection log line is not cited as evidence", !(t.split("Findings and evidence")[1] ?? "").split("Remediation approval")[0].includes("log-514"));
  await shot("inc-1005");

  // ---- unknown incident and API down ------------------------------------------------------
  await goto("/incidents/INC-9999/");
  await sleep(1500);
  t = await text();
  check("unknown incident shows not-found, not a crash", /not found|404/i.test(t));
} catch (e) {
  console.log("ERROR", e.message);
  failures++;
} finally {
  cdp.close();
  proc.kill();
  await sleep(500);
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* browser still releasing files */
  }
}

console.log(failures === 0 ? "\nALL UI CHECKS PASSED" : `\n${failures} UI CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
