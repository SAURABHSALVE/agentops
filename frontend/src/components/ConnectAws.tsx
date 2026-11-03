"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { AccessCode } from "./AccessCode";
import { formatDateTime } from "@/lib/format";
import { friendlyError } from "@/lib/plain";
import type { AwsConnection, LiveIncidents } from "@/lib/types";
import { useAuth } from "@/lib/use-auth";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; connection: AwsConnection };

const REGIONS: [string, string][] = [
  ["us-east-1", "US East (N. Virginia)"],
  ["us-east-2", "US East (Ohio)"],
  ["us-west-1", "US West (N. California)"],
  ["us-west-2", "US West (Oregon)"],
  ["ca-central-1", "Canada (Central)"],
  ["eu-west-1", "Europe (Ireland)"],
  ["eu-west-2", "Europe (London)"],
  ["eu-west-3", "Europe (Paris)"],
  ["eu-central-1", "Europe (Frankfurt)"],
  ["eu-north-1", "Europe (Stockholm)"],
  ["ap-south-1", "Asia Pacific (Mumbai)"],
  ["ap-southeast-1", "Asia Pacific (Singapore)"],
  ["ap-southeast-2", "Asia Pacific (Sydney)"],
  ["ap-northeast-1", "Asia Pacific (Tokyo)"],
  ["ap-northeast-2", "Asia Pacific (Seoul)"],
  ["sa-east-1", "South America (São Paulo)"],
];

const UNAVAILABLE: Record<NonNullable<AwsConnection["reason"]>, string> = {
  not_configured: "Connecting an AWS account is not switched on for this site.",
  sign_in_off: "Connecting an AWS account needs sign-in, which is off for this site.",
  sign_in_required: "Sign in to connect an AWS account.",
};

function problemText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 422) return "Something in the form is not valid. Check the region, the log group names and the role ARN.";
    return friendlyError(e.code, e.message);
  }
  return "Something went wrong. Try again.";
}

/** Connect the signed-in user's own AWS account: agree, create a read-only role from a template, register its ARN. */
export function ConnectAws() {
  const auth = useAuth();
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [reloads, setReloads] = useState(0);
  // Sites that protect writes with a shared access code need it here too, or saving anything is refused.
  const [needsCode, setNeedsCode] = useState(false);
  const ready = auth.status === "signed_in" || auth.status === "off";

  useEffect(() => {
    const ctl = new AbortController();
    api
      .config(ctl.signal)
      .then((c) => setNeedsCode(c.write_access === "code_required"))
      .catch(() => undefined); // optional: the page works without it
    return () => ctl.abort();
  }, []);

  useEffect(() => {
    if (!ready) return;
    const ctl = new AbortController();
    api
      .awsConnection(ctl.signal)
      .then((connection) => setLoad({ state: "ready", connection }))
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setLoad({ state: "error", message: e instanceof ApiError && e.status === 0 ? e.message : "Could not load this page." });
      });
    return () => ctl.abort();
  }, [ready, reloads]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Connect your AWS account</h1>
        <p className="mt-1 text-sm text-muted">
          Give AgentOps read-only access to your own alarms, metrics, recent changes and the log groups you choose, so
          it can investigate a real problem. You create the access yourself in your own account, and you can remove it
          at any time. No access keys are ever shared.
        </p>
        <p className="mt-2 rounded border border-border bg-surface px-3 py-2 text-sm">
          <strong className="font-medium">Nothing is read until you ask.</strong> Once your role is saved, press
          &quot;Check my alarms&quot; to see what is firing. AgentOps only ever reads, and only from your account.
        </p>
      </div>

      {auth.status === "loading" && (
        <div className="h-24 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
      )}

      {auth.status === "signed_out" && (
        <div className="rounded-lg border border-border bg-surface p-5">
          <p className="font-medium">{UNAVAILABLE.sign_in_required}</p>
          <p className="mt-1 text-sm text-muted">The connection belongs to your account and is private to you.</p>
          <button
            type="button"
            onClick={() => auth.signIn("/connect/")}
            className="mt-3 rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg"
          >
            Sign in
          </button>
        </div>
      )}

      {auth.status === "unavailable" && (
        <p className="rounded-lg border border-border bg-surface p-5 text-sm text-muted">
          Sign-in is required here but is not set up on this site.
        </p>
      )}

      {ready && load.state === "loading" && (
        <div
          role="status"
          aria-label="Loading"
          className="h-24 rounded-lg border border-border bg-surface motion-safe:animate-pulse"
        />
      )}

      {ready && load.state === "error" && (
        <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 p-5 text-sm">
          <p className="font-medium text-danger">{load.message}</p>
          <button
            type="button"
            onClick={() => {
              setLoad({ state: "loading" });
              setReloads((n) => n + 1);
            }}
            className="mt-2 text-sm underline"
          >
            Try again
          </button>
        </div>
      )}

      {ready && load.state === "ready" && !load.connection.available && (
        <p className="rounded-lg border border-border bg-surface p-5 text-sm text-muted">
          {load.connection.reason ? UNAVAILABLE[load.connection.reason] : "Not available."}
        </p>
      )}

      {ready && needsCode && load.state === "ready" && load.connection.available && (
        <AccessCode onSaved={() => setReloads((n) => n + 1)} />
      )}

      {ready && load.state === "ready" && load.connection.available && (
        <Workflow
          key={`${load.connection.updated_at ?? "new"}|${load.connection.status}`}
          connection={load.connection}
          onChange={(connection) => setLoad({ state: "ready", connection })}
        />
      )}
    </div>
  );
}

function Workflow({ connection, onChange }: { connection: AwsConnection; onChange: (c: AwsConnection) => void }) {
  return (
    <>
      <Permissions connection={connection} />
      <StepOne connection={connection} onChange={onChange} />
      {connection.status !== "not_connected" && connection.consent_current && (
        <>
          <StepTwo connection={connection} />
          <StepThree connection={connection} onChange={onChange} />
          {connection.status === "registered" && <LiveAlarms onChecked={onChange} />}
          <Disconnect onChange={onChange} />
        </>
      )}
    </>
  );
}

function Permissions({ connection }: { connection: AwsConnection }) {
  return (
    <section aria-labelledby="perm-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="perm-heading" className="text-sm font-semibold">
        What you would be allowing
      </h2>
      <p className="mt-1 text-sm text-muted">
        The role is made only of these read permissions, and the template you download contains exactly this list. It
        cannot change, create or delete anything in your account.
      </p>
      <ul className="mt-3 divide-y divide-border">
        {connection.permissions.map((p) => (
          <li key={p.id} className="py-3">
            <p className="text-sm font-medium">{p.title}</p>
            <p className="text-sm text-muted">{p.why}</p>
            <p className="mt-1 text-xs">
              <span className="font-medium">Reads:</span> {p.reads}
            </p>
            <p className="text-xs">
              <span className="font-medium">Never:</span> {p.never}
            </p>
            <p className="mt-1 text-xs text-muted">
              {p.applies_to === "log_groups" ? "Only the log groups you list below. " : "Across your whole account. "}
              <span className="font-mono">{p.actions.join(", ")}</span>
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function StepOne({ connection, onChange }: { connection: AwsConnection; onChange: (c: AwsConnection) => void }) {
  const [region, setRegion] = useState(connection.region ?? "us-east-1");
  const [groups, setGroups] = useState(connection.log_groups.join("\n"));
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const consent = connection.consent;
  const names = groups
    .split("\n")
    .map((g) => g.trim())
    .filter(Boolean);
  const saved = connection.status !== "not_connected";
  // Someone who already agreed in the current wording is not asked to tick the box again for a small edit.
  const mayAgree = agreed || (saved && connection.consent_current);

  async function save() {
    if (!consent) return;
    setBusy(true);
    setProblem(null);
    try {
      onChange(await api.saveAwsConnection({ region, log_groups: names, consent_version: consent.version }));
    } catch (e) {
      setProblem(problemText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="step1-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="step1-heading" className="text-sm font-semibold">
        Step 1 · Where to look
      </h2>
      <div className="mt-3 space-y-3">
        <div>
          <label htmlFor="aws-region" className="block text-sm font-medium">
            Region
          </label>
          <select
            id="aws-region"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="mt-1 w-full max-w-sm rounded border border-border bg-surface-2 px-2 py-1.5 text-sm"
          >
            {REGIONS.map(([code, label]) => (
              <option key={code} value={code}>
                {label} · {code}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="aws-groups" className="block text-sm font-medium">
            Log groups (one per line, up to 10)
          </label>
          <textarea
            id="aws-groups"
            value={groups}
            onChange={(e) => setGroups(e.target.value)}
            rows={4}
            spellCheck={false}
            placeholder={"/aws/lambda/checkout\n/ecs/payments"}
            className="mt-1 w-full rounded border border-border bg-surface-2 px-2 py-1.5 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-muted">
            Exact names, as they appear in CloudWatch. Only these groups can be searched, and a group created later
            is not covered until you add it here.
          </p>
        </div>

        {consent && (
          <fieldset className="rounded border border-border p-3">
            <legend className="px-1 text-sm font-medium">What you are agreeing to</legend>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {consent.points.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
            {saved && connection.consent_current ? (
              <p className="mt-2 text-xs text-muted">You agreed to this wording.</p>
            ) : (
              <label className="mt-3 flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="mt-1"
                />
                <span>I have read this and agree.</span>
              </label>
            )}
          </fieldset>
        )}

        {saved && (
          <p className="text-xs text-muted">
            Changing the region or the log groups means the role has to be created again (steps 2 and 3).
          </p>
        )}
        {problem && (
          <p role="alert" className="text-sm text-danger">
            {problem}
          </p>
        )}
        <button
          type="button"
          disabled={busy || names.length === 0 || !mayAgree}
          onClick={() => void save()}
          className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-50"
        >
          {busy ? "Saving…" : saved ? "Save changes" : "Save and continue"}
        </button>
      </div>
    </section>
  );
}

function StepTwo({ connection }: { connection: AwsConnection }) {
  const [template, setTemplate] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const setup = connection.setup;

  async function fetchTemplate(): Promise<string | null> {
    setBusy(true);
    setProblem(null);
    try {
      const text = await api.awsConnectionTemplate();
      setTemplate(text);
      return text;
    } catch (e) {
      setProblem(problemText(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    const text = template ?? (await fetchTemplate());
    if (text === null || !setup) return;
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = setup.template_file;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); // not at once: some browsers start the save after the click returns
  }

  async function copy() {
    const text = template ?? (await fetchTemplate());
    if (text === null) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setProblem("Could not copy. Use Download instead.");
    }
  }

  return (
    <section aria-labelledby="step2-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="step2-heading" className="text-sm font-semibold">
        Step 2 · Create the read-only role in your AWS account
      </h2>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
        <li>Download the template below. Look through it first if you like: it is the list above and nothing more.</li>
        <li>
          In the AWS console, open <strong className="font-medium">CloudFormation</strong> in any region, then{" "}
          <em>Create stack → With new resources → Upload a template file</em>.
        </li>
        <li>Give the stack any name (for example agentops-read-only) and continue.</li>
        <li>
          On the last page, tick the box that says CloudFormation may create IAM resources with custom names, and create
          the stack.
        </li>
        <li>
          When it finishes, open the stack&apos;s <em>Outputs</em> tab and copy <span className="font-mono">RoleArn</span>.
        </li>
      </ol>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void download()}
          className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-50"
        >
          Download template
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void copy()}
          className="rounded border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium disabled:opacity-50"
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => (template === null ? void fetchTemplate() : setTemplate(null))}
          className="rounded border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium disabled:opacity-50"
        >
          {template === null ? "Show what is in it" : "Hide"}
        </button>
      </div>
      {problem && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {problem}
        </p>
      )}
      {template !== null && (
        <pre
          aria-label="The CloudFormation template"
          className="mt-3 max-h-96 overflow-auto rounded border border-border bg-surface-2 p-3 text-xs"
        >
          {template}
        </pre>
      )}
      {setup && connection.external_id && (
        <p className="mt-3 text-xs text-muted">
          The role trusts AWS account <span className="font-mono">{setup.trusted_account_id}</span> and only when it
          presents this ExternalId: <span className="font-mono">{connection.external_id}</span>. That stops anyone else
          pointing AgentOps at your role. It is already in the template; you do not type it anywhere.
        </p>
      )}
    </section>
  );
}

function StepThree({ connection, onChange }: { connection: AwsConnection; onChange: (c: AwsConnection) => void }) {
  const [arn, setArn] = useState(connection.role_arn ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const roleName = connection.setup?.role_name ?? "AgentOpsReadOnly";

  async function register() {
    setBusy(true);
    setProblem(null);
    try {
      onChange(await api.registerAwsRole(arn));
    } catch (e) {
      setProblem(problemText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="step3-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="step3-heading" className="text-sm font-semibold">
        Step 3 · Give AgentOps the role&apos;s ARN
      </h2>
      <label htmlFor="aws-role-arn" className="mt-2 block text-sm font-medium">
        Role ARN
      </label>
      <input
        id="aws-role-arn"
        value={arn}
        onChange={(e) => setArn(e.target.value)}
        spellCheck={false}
        placeholder={`arn:aws:iam::123456789012:role/${roleName}`}
        className="mt-1 w-full rounded border border-border bg-surface-2 px-2 py-1.5 font-mono text-sm"
      />
      {problem && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {problem}
        </p>
      )}
      <button
        type="button"
        disabled={busy || arn.trim() === ""}
        onClick={() => void register()}
        className="mt-3 rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-50"
      >
        {busy ? "Saving…" : "Save role"}
      </button>
      {connection.status === "registered" && connection.role_arn && (
        <p role="status" className="mt-3 text-sm">
          Saved <span className="font-mono">{connection.role_arn}</span>.{" "}
          {connection.verified_at ? (
            <span className="text-muted">
              Working: AgentOps used this role successfully on {formatDateTime(connection.verified_at)}.
            </span>
          ) : (
            <span className="text-muted">Not tested yet. Press &quot;Check my alarms&quot; below to try it.</span>
          )}
        </p>
      )}
    </section>
  );
}

function LiveAlarms({ onChecked }: { onChecked: (c: AwsConnection) => void }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<LiveIncidents | null>(null);

  async function check() {
    setBusy(true);
    setProblem(null);
    try {
      setResult(await api.liveIncidents());
      onChecked(await api.awsConnection()); // the role worked, so the page can say so
    } catch (e) {
      setResult(null);
      setProblem(problemText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="alarms-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="alarms-heading" className="text-sm font-semibold">
        Your AWS account (live) · alarms that are firing
      </h2>
      <p className="mt-1 text-sm text-muted">
        This is real data from your own account, read through the role you created. Nothing is changed there.
      </p>
      <button
        type="button"
        disabled={busy}
        onClick={() => void check()}
        className="mt-3 rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-50"
      >
        {busy ? "Checking…" : "Check my alarms"}
      </button>
      {problem && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {problem}
        </p>
      )}
      {result && result.incidents.length === 0 && (
        <p role="status" className="mt-3 text-sm">
          No alarms are firing in {result.region} right now.
        </p>
      )}
      {result && result.incidents.length > 0 && (
        <ul aria-label="Firing alarms" className="mt-3 divide-y divide-border rounded border border-border">
          {result.incidents.map((i) => (
            <li key={i.id} className="p-3">
              <Link href={`/live/?id=${encodeURIComponent(i.id)}`} className="font-medium underline">
                {i.title}
              </Link>
              <p className="mt-0.5 text-xs text-muted">
                {i.service} · since {formatDateTime(i.started_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Disconnect({ onChange }: { onChange: (c: AwsConnection) => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function disconnect() {
    setBusy(true);
    setProblem(null);
    try {
      onChange(await api.disconnectAws());
    } catch (e) {
      setProblem(problemText(e));
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="disc-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="disc-heading" className="text-sm font-semibold">
        Disconnect
      </h2>
      <p className="mt-1 text-sm text-muted">
        AgentOps forgets your role, region and log groups. The role stays in your account until you delete its
        CloudFormation stack there, which is what removes the access completely.
      </p>
      {problem && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {problem}
        </p>
      )}
      {!confirming ? (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="mt-3 rounded border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium hover:border-danger"
        >
          Disconnect…
        </button>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm">Disconnect this AWS account?</span>
          <button
            type="button"
            disabled={busy}
            onClick={() => void disconnect()}
            className="rounded bg-danger px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Disconnecting…" : "Yes, disconnect"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setConfirming(false)}
            className="rounded border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium"
          >
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
