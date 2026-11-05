"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { AccessCode } from "./AccessCode";
import { formatDateTime } from "@/lib/format";
import { friendlyError } from "@/lib/plain";
import type { AwsConnection, LiveIncidents } from "@/lib/types";
import { useAuth } from "@/lib/use-auth";
import { Busy, Icon, Note, Skeleton, Stepper } from "./ui";

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

// The same shapes the server accepts, used only to give early, friendly hints. The server stays the authority.
const LOG_GROUP = /^\/?[A-Za-z0-9_.#/-]{3,200}$/;
const ROLE_ARN = /^arn:aws:iam::\d{12}:role\/[\w+=,.@/-]+$/;

function problemText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 422) return "Something in the form is not valid. Check the region, the log group names and the role ARN.";
    return friendlyError(e.code, e.message);
  }
  return "Something went wrong. Try again.";
}

/** Which of the four steps the person is on, from what is already saved. */
function stepOf(c: AwsConnection): number {
  if (c.status === "not_connected" || !c.consent_current) return 0;
  if (c.status === "draft") return 1;
  return c.verified_at ? 4 : 3;
}

const STEPS = [
  { label: "Choose where to look", hint: "Region and log groups" },
  { label: "Create the role", hint: "In your AWS console" },
  { label: "Paste the role ARN", hint: "One line from AWS" },
  { label: "Check your alarms", hint: "Then investigate one" },
];

/** A section heading with a number badge that turns into a tick once the step is done. */
function StepTitle({ id, done, children }: { id: string; done: boolean; children: React.ReactNode }) {
  return (
    <h2 id={id} className="flex items-center gap-2.5 text-base font-semibold">
      {done && (
        <span className="pop flex h-6 w-6 items-center justify-center rounded-full bg-ok text-white">
          <Icon name="check" className="h-3.5 w-3.5" />
        </span>
      )}
      {children}
    </h2>
  );
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
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Connect your AWS account</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted sm:text-base">
          Give AgentOps read-only access to your own alarms, metrics, recent changes and the log groups you choose, so
          it can investigate a real problem. You create the access yourself in your own account, and you can remove it
          at any time. No access keys are ever shared.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {[
          ["cloud", "About 5 minutes", "Four short steps. You will need an AWS account."],
          [
            "lock",
            "Use an IAM user, not root",
            "Sign in to AWS with an IAM user that can create roles. Never use the root user.",
          ],
          ["shield", "Read-only, and yours to remove", "Delete one CloudFormation stack and the access is gone."],
        ].map(([icon, title, body]) => (
          <div key={title} className="card flex gap-3 p-4">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
              <Icon name={icon as "cloud" | "lock" | "shield"} />
            </span>
            <div>
              <p className="text-sm font-medium">{title}</p>
              <p className="mt-0.5 text-xs text-muted">{body}</p>
            </div>
          </div>
        ))}
      </div>

      <Note tone="info" title="Nothing is read until you ask.">
        Once your role is saved, press &quot;Check my alarms&quot; to see what is firing. AgentOps only ever reads, and
        only from your account.
      </Note>

      {auth.status === "loading" && <Skeleton className="h-24" />}

      {auth.status === "signed_out" && (
        <div className="card p-6">
          <p className="font-medium">{UNAVAILABLE.sign_in_required}</p>
          <p className="mt-1 text-sm text-muted">The connection belongs to your account and is private to you.</p>
          <button type="button" onClick={() => auth.signIn("/connect/")} className="btn btn-primary mt-4">
            Sign in
          </button>
        </div>
      )}

      {auth.status === "unavailable" && (
        <Note tone="warn">Sign-in is required here but is not set up on this site.</Note>
      )}

      {ready && load.state === "loading" && <Skeleton className="h-24" label="Loading" />}

      {ready && load.state === "error" && (
        <div>
          <Note tone="danger" alert title={load.message} />
          <button
            type="button"
            onClick={() => {
              setLoad({ state: "loading" });
              setReloads((n) => n + 1);
            }}
            className="btn btn-secondary mt-3"
          >
            Try again
          </button>
        </div>
      )}

      {ready && load.state === "ready" && !load.connection.available && (
        <Note tone="warn">{load.connection.reason ? UNAVAILABLE[load.connection.reason] : "Not available."}</Note>
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
  const current = stepOf(connection);
  return (
    <div className="enter space-y-4">
      <Stepper steps={STEPS} current={current} label="Setup progress" />
      <Permissions connection={connection} />
      <StepOne connection={connection} onChange={onChange} />
      {connection.status !== "not_connected" && connection.consent_current && (
        <>
          <StepTwo connection={connection} />
          <StepThree connection={connection} onChange={onChange} />
          {connection.status === "registered" && <LiveAlarms connection={connection} onChecked={onChange} />}
          <Disconnect onChange={onChange} />
        </>
      )}
    </div>
  );
}

function Permissions({ connection }: { connection: AwsConnection }) {
  return (
    <details open={connection.status === "not_connected"} className="disclosure card p-5">
      <summary id="perm-heading" className="text-sm font-semibold">
        What you would be allowing
      </summary>
      <p className="mt-2 text-sm text-muted">
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
    </details>
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
  const odd = names.filter((n) => !LOG_GROUP.test(n));
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
    <section aria-labelledby="step1-heading" className="card p-5">
      <StepTitle id="step1-heading" done={saved && connection.consent_current}>
        Step 1 · Where to look
      </StepTitle>
      <div className="mt-4 space-y-4">
        <div>
          <label htmlFor="aws-region" className="block text-sm font-medium">
            Region
          </label>
          <select
            id="aws-region"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="field mt-1 max-w-sm"
          >
            {REGIONS.map(([code, label]) => (
              <option key={code} value={code}>
                {label} · {code}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-muted">The region where your alarms and log groups live. Look at the top right of the AWS console.</p>
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
            className="field mt-1 font-mono"
          />
          <p className="mt-1 text-xs text-muted">
            Exact names, as they appear in CloudWatch. Only these groups can be searched, and a group created later
            is not covered until you add it here.
          </p>
          {names.length > 10 && (
            <p role="status" className="mt-1 text-xs text-warn">
              That is {names.length} groups. Only 10 are allowed, so remove {names.length - 10}.
            </p>
          )}
          {odd.length > 0 && (
            <p role="status" className="mt-1 text-xs text-warn">
              This does not look like a log group name: <span className="font-mono">{odd[0]}</span>. Names look like{" "}
              <span className="font-mono">/aws/lambda/my-function</span>.
            </p>
          )}
          <details className="disclosure mt-2 text-sm">
            <summary className="text-sm text-accent">Where do I find my log group names?</summary>
            <ol className="mt-2 list-decimal space-y-1 pl-9 text-sm text-muted">
              <li>
                In the AWS console, search for <strong className="font-medium text-foreground">CloudWatch</strong> and
                open it (check the region at the top right).
              </li>
              <li>
                In the left menu choose <strong className="font-medium text-foreground">Logs → Log groups</strong>.
              </li>
              <li>
                Copy the name of the group for the service you want to investigate. A Lambda function called{" "}
                <span className="font-mono">checkout</span> logs to <span className="font-mono">/aws/lambda/checkout</span>.
              </li>
              <li>
                Empty list? A Lambda function creates its group the first time it runs. Run it once and refresh.
              </li>
            </ol>
          </details>
        </div>

        {consent && (
          <fieldset className="rounded-xl border border-border bg-surface-2/60 p-4">
            <legend className="px-1 text-sm font-medium">What you are agreeing to</legend>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {consent.points.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
            {saved && connection.consent_current ? (
              <p className="mt-2 text-xs text-muted">You agreed to this wording.</p>
            ) : (
              <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="mt-1 h-4 w-4 accent-[var(--accent)]"
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
          className="btn btn-primary"
        >
          <Busy busy={busy}>{busy ? "Saving…" : saved ? "Save changes" : "Save and continue"}</Busy>
        </button>
        {!saved && names.length > 0 && !mayAgree && (
          <p className="text-xs text-muted">Tick the box above to continue.</p>
        )}
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
      setTimeout(() => setCopied(false), 2500);
    } catch {
      setProblem("Could not copy. Use Download instead.");
    }
  }

  return (
    <section aria-labelledby="step2-heading" className="card p-5">
      <StepTitle id="step2-heading" done={connection.status === "registered"}>
        Step 2 · Create the read-only role in your AWS account
      </StepTitle>
      <ol className="mt-3 space-y-2.5 text-sm">
        {[
          <>Download the template below. Look through it first if you like: it is the list above and nothing more.</>,
          <>
            In the AWS console, open <strong className="font-medium">CloudFormation</strong> in any region, then{" "}
            <em>Create stack → With new resources → Upload a template file</em>.
          </>,
          <>Give the stack any name (for example agentops-read-only) and continue.</>,
          <>
            On the last page, tick the box that says CloudFormation may create IAM resources with custom names, and
            create the stack.
          </>,
          <>
            When it finishes, open the stack&apos;s <em>Outputs</em> tab and copy <span className="font-mono">RoleArn</span>.
          </>,
        ].map((item, i) => (
          <li key={i} className="flex gap-3">
            <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold text-muted">
              {i + 1}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ol>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy} onClick={() => void download()} className="btn btn-primary">
          <Busy busy={busy}>Download template</Busy>
        </button>
        <button type="button" disabled={busy} onClick={() => void copy()} className="btn btn-secondary">
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => (template === null ? void fetchTemplate() : setTemplate(null))}
          className="btn btn-secondary"
        >
          {template === null ? "Show what is in it" : "Hide"}
        </button>
        <a
          href="https://console.aws.amazon.com/cloudformation/home#/stacks/create"
          target="_blank"
          rel="noreferrer noopener"
          className="link ml-1 inline-flex items-center gap-1 text-sm"
        >
          Open CloudFormation
          <Icon name="arrow" className="h-4 w-4 -rotate-45" />
        </a>
      </div>
      {problem && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {problem}
        </p>
      )}
      {template !== null && (
        <pre
          aria-label="The CloudFormation template"
          className="mt-3 max-h-96 overflow-auto rounded-xl border border-border bg-surface-2 p-3 text-xs"
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
      <details className="disclosure mt-3">
        <summary className="text-sm text-accent">Something went wrong creating the stack?</summary>
        <ul className="mt-2 list-disc space-y-1 pl-9 text-sm text-muted">
          <li>
            <strong className="font-medium text-foreground">Role already exists:</strong> an earlier AgentOps stack
            already made it. Delete that CloudFormation stack, then create this one.
          </li>
          <li>
            <strong className="font-medium text-foreground">Needs capabilities / IAM:</strong> tick the box on the last
            page that lets CloudFormation create IAM resources.
          </li>
          <li>
            <strong className="font-medium text-foreground">Access denied:</strong> your AWS user is not allowed to
            create roles. Ask an administrator, or use an IAM user with administrator access.
          </li>
        </ul>
      </details>
    </section>
  );
}

function StepThree({ connection, onChange }: { connection: AwsConnection; onChange: (c: AwsConnection) => void }) {
  const [arn, setArn] = useState(connection.role_arn ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const roleName = connection.setup?.role_name ?? "AgentOpsReadOnly";
  const trimmed = arn.trim();

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
    <section aria-labelledby="step3-heading" className="card p-5">
      <StepTitle id="step3-heading" done={connection.status === "registered"}>
        Step 3 · Give AgentOps the role&apos;s ARN
      </StepTitle>
      <label htmlFor="aws-role-arn" className="mt-3 block text-sm font-medium">
        Role ARN
      </label>
      <input
        id="aws-role-arn"
        value={arn}
        onChange={(e) => setArn(e.target.value)}
        spellCheck={false}
        placeholder={`arn:aws:iam::123456789012:role/${roleName}`}
        className="field mt-1 font-mono"
      />
      {trimmed !== "" && (
        <p role="status" className={`mt-1 text-xs ${ROLE_ARN.test(trimmed) ? "text-ok" : "text-warn"}`}>
          {ROLE_ARN.test(trimmed)
            ? "That looks like a role ARN."
            : `A role ARN looks like arn:aws:iam::123456789012:role/${roleName}. Copy RoleArn from the stack's Outputs tab.`}
        </p>
      )}
      {problem && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {problem}
        </p>
      )}
      <button
        type="button"
        disabled={busy || trimmed === ""}
        onClick={() => void register()}
        className="btn btn-primary mt-3"
      >
        <Busy busy={busy}>{busy ? "Saving…" : "Save role"}</Busy>
      </button>
      {connection.status === "registered" && connection.role_arn && (
        <p role="status" className="mt-3 text-sm">
          Saved <span className="break-all font-mono">{connection.role_arn}</span>.{" "}
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

function LiveAlarms({
  connection,
  onChecked,
}: {
  connection: AwsConnection;
  onChecked: (c: AwsConnection) => void;
}) {
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
    <section aria-labelledby="alarms-heading" className="card p-5">
      <StepTitle id="alarms-heading" done={connection.verified_at !== null}>
        Your AWS account (live) · alarms that are firing
      </StepTitle>
      <p className="mt-1 text-sm text-muted">
        This is real data from your own account, read through the role you created. Nothing is changed there.
      </p>
      <button type="button" disabled={busy} onClick={() => void check()} className="btn btn-primary mt-3">
        <Busy busy={busy}>{busy ? "Checking…" : result ? "Check again" : "Check my alarms"}</Busy>
      </button>
      {problem && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {problem}
        </p>
      )}
      {result && result.incidents.length === 0 && (
        <div className="enter mt-4 space-y-3">
          <Note tone="ok">
            <span role="status">
              No alarms are firing in {result.region} right now. That is good news, and it means the connection works.
            </span>
          </Note>
          <details className="disclosure text-sm">
            <summary className="text-sm text-accent">Want something to investigate? Make a test alarm</summary>
            <ol className="mt-2 list-decimal space-y-1 pl-9 text-sm text-muted">
              <li>
                In CloudWatch open <strong className="font-medium text-foreground">Alarms → Create alarm</strong>.
              </li>
              <li>
                Choose any metric that has data, for example a Lambda function&apos;s <span className="font-mono">Errors</span>
                , or an EC2 instance&apos;s <span className="font-mono">CPUUtilization</span>.
              </li>
              <li>Set a threshold that is easy to cross (for example errors greater than 0, or CPU above 1%).</li>
              <li>Create it, wait a few minutes until it shows <em>In alarm</em>, then press Check again here.</li>
              <li>Delete the test alarm afterwards so it does not keep firing.</li>
            </ol>
          </details>
        </div>
      )}
      {result && result.incidents.length > 0 && (
        <div className="enter mt-4">
          <p className="mb-2 text-sm text-muted">Pick an alarm to investigate it:</p>
          <ul aria-label="Firing alarms" className="space-y-2">
            {result.incidents.map((i) => (
              <li key={i.id}>
                <Link
                  href={`/live/?id=${encodeURIComponent(i.id)}`}
                  className="card card-interactive group flex items-center justify-between gap-3 p-3.5"
                >
                  <span className="min-w-0">
                    <span className="block font-medium">{i.title}</span>
                    <span className="mt-0.5 block text-xs text-muted">
                      {i.service} · since {formatDateTime(i.started_at)}
                    </span>
                  </span>
                  <Icon name="arrow" className="h-4 w-4 shrink-0 text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
                </Link>
              </li>
            ))}
          </ul>
        </div>
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
    <section aria-labelledby="disc-heading" className="card p-5">
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
        <button type="button" onClick={() => setConfirming(true)} className="btn btn-danger-outline mt-3">
          Disconnect…
        </button>
      ) : (
        <div className="enter mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm">Disconnect this AWS account?</span>
          <button type="button" disabled={busy} onClick={() => void disconnect()} className="btn btn-danger">
            <Busy busy={busy}>{busy ? "Disconnecting…" : "Yes, disconnect"}</Busy>
          </button>
          <button type="button" disabled={busy} onClick={() => setConfirming(false)} className="btn btn-secondary">
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
