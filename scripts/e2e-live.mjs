// Checks the DEPLOYED site in a real browser, signed out, without creating anything or changing anything.
//
//   node scripts/e2e-live.mjs https://dxxxx.cloudfront.net [--signed-in-token-file path]
//
// It loads the pages the way a first-time visitor would, on a desktop and a phone-sized window, and fails on:
// an uncaught script error, a console error, a blocked request (Content-Security-Policy), horizontal scrolling on a
// phone, a missing incident, or a sign-in button that does not reach Cognito's hosted page. It never submits a form.
// Zero dependencies (Node 22+). Env: CHROME (browser path), SHOTS (directory for screenshots).

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SITE = (process.argv[2] ?? "").replace(/\/$/, "");
if (!/^https:\/\/[A-Za-z0-9.-]+$/.test(SITE)) {
  console.error("usage: node scripts/e2e-live.mjs https://<site>");
  process.exit(2);
}
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

const exe = CANDIDATES.find((p) => existsSync(p));
if (!exe) throw new Error("No Chrome/Edge found; set CHROME to a browser executable.");
const dir = mkdtempSync(join(tmpdir(), "agentops-live-"));
const port = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(exe, [`--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, "--headless=new", "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
let wsUrl;
for (let i = 0; i < 50 && !wsUrl; i++) {
  try {
    wsUrl = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page")?.webSocketDebuggerUrl;
  } catch {
    /* not up yet */
  }
  if (!wsUrl) await sleep(200);
}
if (!wsUrl) throw new Error("Browser did not start.");

const ws = new WebSocket(wsUrl);
let nextId = 0;
const pending = new Map();
const problems = []; // anything the page itself complained about
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  } else if (msg.method === "Runtime.exceptionThrown") {
    problems.push(`exception: ${msg.params.exceptionDetails.exception?.description ?? msg.params.exceptionDetails.text}`);
  } else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
    problems.push(`log: ${msg.params.entry.text} ${msg.params.entry.url ?? ""}`);
  } else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
    problems.push(`console: ${msg.params.args.map((a) => a.value ?? a.description).join(" ")}`);
  }
};
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
for (const m of ["Page.enable", "Runtime.enable", "Log.enable", "Network.enable"]) await send(m);

const evaluate = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result.value;
};
const text = () => evaluate("document.body ? document.body.innerText : ''").catch(() => ""); // a page mid-navigation has no body yet
async function waitText(needle, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if ((await text()).includes(needle)) return true;
    await sleep(250);
  }
  return false;
}
async function goto(path) {
  const t0 = Date.now();
  await send("Page.navigate", { url: SITE + path });
  await sleep(400);
  return () => Date.now() - t0;
}
async function viewport(width, height, mobile) {
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
}
const sideScroll = () => evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
async function shot(name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(join(SHOTS, `${name}.png`), Buffer.from(data, "base64"));
}
const clickButton = (label) =>
  evaluate(`(() => { const b=[...document.querySelectorAll('button,a')].find(b=>b.innerText.trim().startsWith(${JSON.stringify(label)})); if(!b||b.disabled) return false; b.click(); return true; })()`);

try {
  // ---- a first-time visitor on a laptop -------------------------------------------------------------
  await viewport(1280, 900, false);
  let took = await goto("/");
  check("home page loads and lists the incidents", await waitText("Showing 6 of 6 incidents", 20000));
  console.log(`      (home page ready in ${took()} ms)`);
  check("it says who it is for and what it does", /why something broke/i.test(await text()));
  check("header offers Sign in", (await text()).includes("Sign in"));
  check("the data is labelled as simulated", (await text()).includes("Simulated demo telemetry"));
  await shot("live-home");

  took = await goto("/incidents/INC-1001/");
  check("an incident opens (not 'Incident not found')", await waitText("Elevated HTTP 5xx on checkout", 20000) && !(await text()).includes("Incident not found"));
  console.log(`      (incident page ready in ${took()} ms)`);
  check("a visitor is invited to sign in to investigate", await waitText("Sign in to investigate", 10000));
  check("it explains that investigations are private", (await text()).includes("private to your account"));
  check("the timeline shows the incident history", (await text()).includes("Alert fired"));
  await shot("live-incident");

  for (const id of ["INC-1002", "INC-1003", "INC-1004", "INC-1005", "INC-1006"]) {
    await goto(`/incidents/${id}/`);
    const ok = await waitText("Sign in to investigate", 20000);
    check(`${id} opens for a visitor`, ok && !(await text()).includes("Incident not found"));
  }

  await goto("/incidents/INC-9999/");
  check("an unknown incident is a clear not-found", await waitText("not found", 15000));

  // ---- a first-time visitor on a phone ----------------------------------------------------------------
  await viewport(390, 844, true);
  for (const [name, path, marker] of [
    ["home", "/", "Showing 6 of 6 incidents"],
    ["incident", "/incidents/INC-1001/", "Sign in to investigate"],
  ]) {
    await goto(path);
    check(`phone: ${name} renders`, await waitText(marker, 20000));
    const over = await sideScroll();
    check(`phone: ${name} does not scroll sideways`, over <= 1, `(${over}px too wide)`);
    await shot(`live-phone-${name}`);
  }

  // ---- sign in goes to the real hosted page -----------------------------------------------------------
  await viewport(1280, 900, false);
  await goto("/");
  await waitText("Showing 6 of 6 incidents", 20000);
  check("Sign in button is clickable", await clickButton("Sign in"));
  let landed = "";
  for (let i = 0; i < 40 && !landed.includes("amazoncognito.com"); i++) {
    await sleep(500);
    landed = await evaluate("location.href");
  }
  check("it reaches Cognito's hosted page", landed.includes(".auth.us-east-1.amazoncognito.com/login"), landed.slice(0, 120));
  check("the hosted page offers sign-in and sign-up", (await waitText("Sign up", 15000)) && /sign in/i.test(await text()));
  check("the request carries PKCE and state", /code_challenge=/.test(landed) && /state=/.test(landed) && /code_challenge_method=S256/.test(landed));
  await shot("live-hosted-login");

  // ---- the page itself raised nothing ------------------------------------------------------------------
  const own = problems.filter((p) => !/amazoncognito\.com/.test(p));
  check("no script errors, console errors or blocked requests on the site", own.length === 0, own.slice(0, 3).join(" | "));
} catch (e) {
  console.log("ERROR", e.message);
  failures++;
} finally {
  ws.close();
  proc.kill();
  await sleep(500);
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* browser still releasing files */
  }
}
console.log(failures === 0 ? "\nLIVE SITE: ALL CHECKS PASSED" : `\nLIVE SITE: ${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
