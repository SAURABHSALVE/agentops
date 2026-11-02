// End-to-end UI check, driven through the Chrome DevTools Protocol. Zero dependencies (Node 22+).
//
//   1. start the API:       cd backend && .venv\Scripts\python -m uvicorn app.main:app --port 8000
//      (use a throwaway LOCAL_DATA_DIR and AGENT_PROVIDER=mock for the deterministic run)
//   2. start the frontend:  cd frontend && npm run dev
//   3. run:                 node scripts/e2e-ui.mjs [--live]
//
// It clicks through the real UI against the real API and exits non-zero on the first failed expectation.
// Env: CHROME (browser path), UI (default http://localhost:3000), SHOTS (directory for screenshots).
// CAPTURED=1 expects the API to serve the captured lab dataset (see docs/REAL_TELEMETRY.md).
// --live expects the API to run with AGENT_PROVIDER=openai and only checks properties that hold for a real model.
// AUTH=1 checks sign-in end to end against a fake Cognito, starting its own API and serving frontend/out
// (run `npm run build` in frontend/ first); it needs nothing else running. See scripts/e2e-auth-harness.mjs.

import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let UI = process.env.UI ?? "http://localhost:3000";
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
  if (process.env.AUTH) {
    const { startHarness, USERS } = await import("./e2e-auth-harness.mjs");
    const h = await startHarness();
    UI = h.siteUrl;
    try {
      const evalJson = (expr) => evaluate(`(async () => JSON.stringify(await (${expr})))()`).then(JSON.parse);
      const asVisitor = (method, path) =>
        evalJson(`fetch(${JSON.stringify(path)}, { method: ${JSON.stringify(method)} }).then(async r => ({ status: r.status, body: await r.json() }))`);

      // ---- a visitor can read, but not write ---------------------------------------------------
      await goto("/");
      check("visitor sees the incident list", await waitText("Showing 6 of 6 incidents"));
      check("header offers sign in", (await text()).includes("Sign in"));
      await goto("/incidents/INC-1001/");
      check("incident page loads for a visitor", await waitText("Elevated HTTP 5xx on checkout"));
      check("investigating asks the visitor to sign in", await waitText("Sign in to investigate"));
      check("no investigation is shown to a visitor", (await text()).includes("No investigation has been run"));
      const refused = await asVisitor("POST", "/api/incidents/INC-1001/investigations");
      check("the API itself refuses an unauthenticated write", refused.status === 401 && refused.body.error.code === "sign_in_required");
      await shot("auth-signed-out");

      // ---- alice signs in through the hosted page and back -------------------------------------
      h.state.user = USERS.alice;
      check("header Sign in starts the hosted flow", await clickButton("Sign in"));
      check("alice lands back on the incident page, signed in", (await waitText(USERS.alice.email, 20000)) && (await waitText("Elevated HTTP 5xx on checkout", 20000)));
      await waitText("No investigation has been run", 20000);
      check("the code was exchanged exactly once", h.state.issued === 1);
      check("sign-out is offered", (await text()).includes("Sign out"));
      check("the investigate button replaces the sign-in prompt", (await text()).includes("Investigate") && !(await text()).includes("Sign in to investigate"));
      await clickButton("Investigate");
      check("alice's investigation finishes", await waitText("Finished after", 90000));
      check("alice approves and verification passes", (await clickButton("Approve simulated action")) && (await waitText("Passed: incident resolved", 20000)));
      await shot("auth-alice-resolved");
      await goto("/incidents/INC-1001/");
      check("alice's record survives a reload", (await waitText("Passed: incident resolved", 20000)) && (await text()).includes(USERS.alice.email));
      await goto("/");
      await waitText("Showing 6 of 6 incidents");
      check("the list shows INC-1001 resolved for alice", /INC-1001[\s\S]{0,200}Resolved/i.test(await text()));

      // ---- "my investigations" ------------------------------------------------------------------
      check("the header links to My investigations", (await text()).includes("My investigations"));
      await goto("/");
      check("home says how many investigations alice has", await waitText("You have 1 investigation", 20000));
      await goto("/investigations/");
      check("My investigations lists alice's run and what happened to the fix", (await waitText("Fix approved, and the incident is resolved", 20000)) && (await text()).includes("INC-1001") && (await text()).includes("Confidence: high"));
      await shot("auth-my-investigations");

      // ---- signing out hides it again ----------------------------------------------------------
      check("sign out works", await clickButton("Sign out"));
      check("back at the dashboard, signed out", (await waitText("Showing 6 of 6 incidents", 20000)) && !(await text()).includes(USERS.alice.email));
      await goto("/incidents/INC-1001/");
      check("a signed-out visitor sees none of alice's investigation", (await waitText("No investigation has been run", 20000)) && !(await text()).includes("Passed: incident resolved"));
      check("...and INC-1001 is not resolved for a visitor", !/Resolved/.test((await text()).split("Investigation")[0]));

      await goto("/investigations/");
      check("a visitor is asked to sign in to see investigations", await waitText("Sign in to see your investigations", 15000));

      // ---- bob is a different person -----------------------------------------------------------
      h.state.user = USERS.bob;
      await clickButton("Sign in");
      check("bob signs in", await waitText(USERS.bob.email, 20000));
      await goto("/");
      check("a brand-new account is welcomed and pointed at a first incident", (await waitText("Welcome!", 15000)) && (await text()).includes("Start with INC-1001"));
      await shot("auth-welcome");
      await goto("/incidents/INC-1001/");
      check("bob does not see alice's investigation", (await waitText("No investigation has been run", 20000)) && !(await text()).includes("Passed: incident resolved"));
      check("INC-1001 is open for bob", !/Resolved/.test((await text()).split("Investigation")[0]));
      await clickButton("Investigate");
      check("bob's own investigation finishes", await waitText("Finished after", 90000));
      check("bob's approval really runs (alice's did not do it for him)", (await clickButton("Approve simulated action")) && (await waitText("Passed: incident resolved", 20000)));
      await shot("auth-bob-resolved");
      await goto("/investigations/");
      check("bob's list holds exactly his own run", (await waitText("Fix approved, and the incident is resolved", 20000)) && (await evaluate(`document.querySelectorAll('ul[aria-label="Your investigations"] li').length`)) === 1);
      await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
      await goto("/investigations/");
      await waitText("Fix approved, and the incident is resolved", 20000);
      const wide = await evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
      check("phone: My investigations does not scroll sideways", wide <= 1, `(${wide}px too wide)`);
      await shot("auth-my-investigations-phone");
      await cdp.send("Emulation.clearDeviceMetricsOverride");

      // alice comes back and deletes hers; bob's must survive
      check("bob signs out", await clickButton("Sign out"));
      await waitText("Showing 6 of 6 incidents", 20000);
      h.state.user = USERS.alice;
      await clickButton("Sign in");
      check("alice signs in again", await waitText(USERS.alice.email, 20000));
      await goto("/investigations/");
      check("alice still has her run", await waitText("Fix approved, and the incident is resolved", 20000));
      check("deleting asks first", (await clickButton("Delete…")) && (await waitText("This cannot be undone", 10000)));
      check("cancel keeps everything", (await clickButton("Cancel")) && (await text()).includes("Fix approved, and the incident is resolved"));
      await clickButton("Delete…");
      await waitText("This cannot be undone", 10000);
      check("confirming deletes", (await clickButton("Yes, delete")) && (await waitText("Deleted 1 investigation", 20000)));
      check("her list is now empty", await waitText("You have not run an investigation yet", 20000));
      await goto("/incidents/INC-1001/");
      check("and the incident shows nothing of hers", (await waitText("No investigation has been run", 20000)) && !(await text()).includes("Passed: incident resolved"));
      await clickButton("Sign out");
      await waitText("Showing 6 of 6 incidents", 20000);
      h.state.user = USERS.bob;
      await clickButton("Sign in");
      await waitText(USERS.bob.email, 20000);
      await goto("/investigations/");
      check("bob's investigation survived alice's deletion", await waitText("Fix approved, and the incident is resolved", 20000));

      // ---- a token the server will not accept -------------------------------------------------
      await evaluate(`(() => { const s = JSON.parse(sessionStorage.getItem("agentops-session")); s.accessToken = "forged.token.value"; sessionStorage.setItem("agentops-session", JSON.stringify(s)); })()`);
      await goto("/incidents/INC-1001/");
      check("a rejected token does not break reading: the page still loads", await waitText("Elevated HTTP 5xx on checkout", 20000));
      check("and the browser drops that sign-in", (await waitText("Sign in to investigate", 20000)) && !(await text()).includes(USERS.bob.email));

      // ---- the production build answers an unknown incident with a plain not-found page -------------
      await goto("/incidents/INC-9999/");
      check("an unknown incident is a clean not-found in the built site", await waitText("not found", 15000));

      // ---- the callback refuses bad arrivals ----------------------------------------------------
      await goto("/auth/callback?error=access_denied");
      check("cancelling at the hosted page is explained", await waitText("Sign-in was cancelled."));
      await goto("/auth/callback?code=stolen&state=forged");
      check("a callback without a matching state is refused", await waitText("not valid or was already used"));
      check("no session came out of it", !(await text()).includes("@example.com"));
      await shot("auth-callback-refused");
    } finally {
      await h.stop();
    }
    throw new Error("__done__");
  }
  if (process.env.API_DOWN) {
    // Run with the API stopped: the UI must say so plainly instead of showing a blank or broken page.
    await goto("/");
    check("dashboard explains that the API is unreachable", await waitText("Could not load incidents", 15000));
    check("it names the likely cause and offers a retry", (await text()).includes("Is the backend running?") && (await text()).includes("Try again"));
    await goto("/incidents/INC-1001/");
    check("incident page shows a clear error", await waitText("Could not load this incident", 15000));
    await shot("api-down");
    throw new Error("__done__");
  }
  if (process.env.GATED) {
    // Run with the API started with ACCESS_CODE=<code>; pass the same value as GATED.
    await goto("/incidents/INC-1001/");
    check("access-code form is shown when the deployment needs one", await waitText("Access code (needed to run investigations"));
    await clickButton("Investigate");
    check("a write without the code is refused with a clear message", await waitText("An access code is required", 15000));
    check("nothing was started", (await text()).includes("No investigation has been run"));
    const filled = await evaluate(`(() => { const i=document.querySelector('input[type=password]'); const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; set.call(i, ${JSON.stringify(process.env.GATED)}); i.dispatchEvent(new Event('input',{bubbles:true})); return true; })()`);
    check("code can be typed", filled);
    await clickButton("Save");
    await sleep(300);
    await clickButton("Investigate");
    check("with the code the investigation runs", await waitText("Finished after", 60000));
    check("approval works with the code", (await clickButton("Approve simulated action")) && (await waitText("Passed: incident resolved", 20000)));
    await shot("gated");
    throw new Error("__done__");
  }
  if (process.env.CAPTURED) {
    // Run with the API started with TELEMETRY_DATASET=data/captured/lab-capture.json (fresh LOCAL_DATA_DIR).
    await goto("/");
    check("dashboard lists only the captured incidents", await waitText("Showing 3 of 3 incidents"));
    check("header says the data was captured from a lab system", await waitText("captured from a lab system"));
    let t = await text();
    check("the three lab incidents are listed", ["INC-2001", "INC-2002", "INC-2003"].every((id) => t.includes(id)) && !t.includes("INC-1001"));
    await shot("captured-dashboard");

    await goto("/incidents/INC-2001/");
    check("INC-2001 loads", await waitText("Elevated HTTP 5xx on checkout"));
    await clickButton("Investigate");
    check("INC-2001 investigation finishes on real telemetry", await waitText("Finished after", 90000));
    t = await text();
    check("evidence cites ids from the captured logs and metrics", /log-2\d{3}/.test(t) && t.includes("metric:"));
    check("the evidence mentions the real failing dependency", /payments-gateway/i.test(t));
    await shot("captured-inc-2001-findings");
    if (await clickButton("Approve simulated action")) {
      check("INC-2001 verification passes from measured recovery", await waitText("Passed: incident resolved", 20000));
      await shot("captured-inc-2001-resolved");
    } else check("INC-2001 proposed a remediation to approve", false);

    await goto("/incidents/INC-2003/");
    await waitText("Worker queue backlog growing");
    await clickButton("Investigate");
    check("INC-2003 investigation finishes", await waitText("Finished after", 90000));
    if (await clickButton("Approve simulated action")) {
      check("INC-2003: the restart runs but verification FAILS (backlog is still there)", await waitText("FAILED: incident NOT resolved", 20000));
      t = await text();
      check("INC-2003 is not shown as resolved", !t.includes("Passed: incident resolved") && !/Resolved/.test(t.split("Investigation")[0]));
      await shot("captured-inc-2003-not-resolved");
    } else if (LIVE) check("INC-2003 proposed a remediation to approve", false);
    else {
      // The mock's rules were written for the simulated fixtures (docs/REAL_TELEMETRY.md); only a live model proposes here.
      check("mock offers no action on INC-2003 and says so (run with --live to exercise the failed verification)", (await text()).includes("No remediation proposed"));
    }

    await goto("/incidents/INC-1001/");
    check("a simulated incident id is not served in captured mode", await waitText("Incident not found", 15000));
    throw new Error("__done__");
  }
  // ---- dashboard -------------------------------------------------------------------------
  await goto("/");
  check("dashboard lists the incidents from the API", await waitText("Showing 6 of 6 incidents"));
  check("dashboard is labelled as simulated telemetry", await waitText("Simulated demo telemetry"));
  const dash = await text();
  check("stale Bedrock sentence is gone", !/bedrock/i.test(dash));
  await shot("dashboard");

  // ---- clear incident: investigate -> approve -> resolved -----------------------------------
  await goto("/incidents/INC-1001/");
  check("incident page loads from the API", await waitText("Elevated HTTP 5xx on checkout"));
  check("no investigation yet", await waitText("No investigation has been run"));
  check("investigate button works", await clickButton("Investigate"));
  check("investigation finishes", await waitText("Finished after", 90000));
  let t = await text();
  check("provider is shown", LIVE ? t.includes("OpenAI") : t.includes("not an LLM"));
  check("tool trace shows real tools", t.includes("get_incident(") && t.includes("search_logs("));
  check("findings cite evidence ids", /log-1\d\d/.test(t) && t.includes("metric:"));
  check("remediation proposed, not run", t.includes("simulate_clear_dependency_fault") && t.includes("Approve simulated action"));
  await shot("inc-1001-findings");
  check("feedback is offered once findings exist", (await text()).includes("Was this answer useful?"));
  await clickButton("Yes, helpful");
  check("feedback can be sent", (await clickButton("Send feedback")) && (await waitText("Thanks, feedback saved.", 15000)));
  check("approve works", await clickButton("Approve simulated action"));
  check("verification passes", await waitText("Passed: incident resolved", 20000));
  t = await text();
  check("incident status becomes Resolved", /Resolved/.test(t.split("Investigation")[0]));
  check("timeline records approval, action, verification", t.includes("Remediation approved") && t.includes("Simulated action succeeded") && t.includes("Verification passed"));
  await shot("inc-1001-resolved");

  await goto("/incidents/INC-1001/");
  check("state survives a page reload", (await waitText("Passed: incident resolved", 15000)) && (await text()).includes("get_incident("));
  check("feedback survives a reload too", (await text()).includes("Thanks, feedback saved."));

  // ---- verification failure --------------------------------------------------------------
  await goto("/incidents/INC-1006/");
  await waitText("Worker backlog persists");
  await clickButton("Investigate");
  check("INC-1006 investigation finishes", await waitText("Finished after", 90000));
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
  check("INC-1004 investigation finishes", await waitText("Finished after", 90000));
  t = await text();
  if (!LIVE) check("ambiguous incident: low confidence and no remediation", /confidence: low/i.test(t) && t.includes("No remediation proposed"));
  else check("ambiguous incident: no remediation is offered", t.includes("No remediation proposed") || !t.includes("Approve simulated action"));
  await shot("inc-1004");

  // ---- rejection --------------------------------------------------------------------------
  await goto("/incidents/INC-1002/");
  await waitText("Catalog latency elevated");
  await clickButton("Investigate");
  await waitText("Finished after", 90000);
  if (await clickButton("Reject")) {
    check("reject works and nothing runs", await waitText("Rejected. The action was not executed", 15000));
    check("rejection is on the timeline", (await text()).includes("Remediation rejected"));
  } else check("INC-1002 offered a remediation to reject", LIVE, "(model proposed none)");

  // ---- injection --------------------------------------------------------------------------
  await goto("/incidents/INC-1005/");
  await waitText("Checkout order confirmations failing");
  await clickButton("Investigate");
  check("INC-1005 investigation finishes", await waitText("Finished after", 90000));
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
  if (e.message !== "__done__") {
    console.log("ERROR", e.message);
    failures++;
  }
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
