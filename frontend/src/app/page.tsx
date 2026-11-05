import { DemoLink } from "@/components/DemoLink";
import { IncidentDashboard } from "@/components/IncidentDashboard";
import { StartPaths } from "@/components/StartPaths";
import { Icon } from "@/components/ui";
import { Welcome } from "@/components/Welcome";

const STEPS = [
  {
    icon: "list",
    title: "Pick an incident",
    body: "Something is broken: errors are up, a queue is backing up, a page got slow.",
  },
  {
    icon: "search",
    title: "The agent investigates",
    body: "It reads the logs, metrics and recent releases for you, and shows every check it made.",
  },
  {
    icon: "shield",
    title: "You decide",
    body: "You get a likely cause with proof and a confidence level. A fix only runs if you approve it, and the result is checked.",
  },
] as const;

const SAMPLE_EVIDENCE = [
  ["Logs", "42 “payments timeout” errors, starting at 14:02"],
  ["Metrics", "Error rate 9× its normal level, still elevated"],
  ["Releases", "No change in the hour before it began"],
] as const;

/** A fixed example of what an answer looks like, so a first-time visitor knows what they will get. */
function SampleAnswer() {
  return (
    <div className="card relative overflow-hidden p-5" aria-label="Example of an answer">
      <span className="absolute right-4 top-4 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted">
        Example
      </span>
      <p className="eyebrow">Most likely cause</p>
      <p className="mt-1.5 pr-14 text-[15px] font-semibold leading-snug">
        Calls to the payments service are timing out, so checkout requests fail.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <span className="flex gap-1" aria-hidden="true">
          <span className="h-1.5 w-7 rounded-full bg-ok" />
          <span className="h-1.5 w-7 rounded-full bg-ok" />
          <span className="h-1.5 w-7 rounded-full bg-ok" />
        </span>
        <span className="text-xs font-medium text-ok">High confidence</span>
      </div>
      <ul className="mt-4 space-y-2 text-sm">
        {SAMPLE_EVIDENCE.map(([kind, text]) => (
          <li key={kind} className="flex items-start gap-2.5">
            <span className="mt-0.5 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent">
              {kind}
            </span>
            <span className="text-muted">{text}</span>
          </li>
        ))}
      </ul>
      <p className="mt-4 border-t border-border pt-3 text-xs text-muted">
        Every line points at something the agent actually looked at. If it is not sure, it says so.
      </p>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <div className="space-y-12">
      <section aria-labelledby="hero-heading" className="grid items-center gap-8 lg:grid-cols-[1.15fr_1fr]">
        <div>
          <p className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted shadow-sm">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
            AI incident investigator
          </p>
          <h1 id="hero-heading" className="mt-4 text-3xl font-semibold tracking-tight sm:text-5xl sm:leading-[1.08]">
            Find out why something broke,{" "}
            <span
              className="bg-clip-text text-transparent"
              style={{ backgroundImage: "linear-gradient(120deg, var(--accent), #c084fc 70%, #f472b6)" }}
            >
              with proof.
            </span>
          </h1>
          <p className="mt-4 max-w-xl text-base text-muted sm:text-lg">
            AgentOps reads your logs, metrics and recent releases, then tells you the most likely cause, how sure it is,
            and what to do next. When it is not sure, it says so instead of guessing.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <DemoLink />
            <a href="#incidents-heading" className="btn btn-secondary btn-lg">
              Browse incidents
            </a>
          </div>
          <p className="mt-4 flex items-start gap-2 text-xs text-muted">
            <Icon name="lock" className="mt-px h-3.5 w-3.5 shrink-0" />
            The demo uses fictional or lab-made incidents. No real systems are touched, and nothing changes without your
            approval.
          </p>
        </div>
        <SampleAnswer />
      </section>

      <Welcome />

      <StartPaths />

      <section aria-labelledby="how-heading">
        <h2 id="how-heading" className="eyebrow">
          How it works
        </h2>
        <ol className="enter-stagger mt-3 grid gap-3 sm:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="card p-5">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <Icon name={s.icon} />
              </span>
              <p className="mt-3 font-medium">
                <span className="text-muted">{i + 1}.</span> {s.title}
              </p>
              <p className="mt-1 text-sm text-muted">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <IncidentDashboard />
    </div>
  );
}
