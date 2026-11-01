import { DemoLink } from "@/components/DemoLink";
import { IncidentDashboard } from "@/components/IncidentDashboard";

const STEPS = [
  {
    title: "Pick an incident",
    body: "Something is broken: errors are up, a queue is backing up, a page got slow.",
  },
  {
    title: "The agent investigates",
    body: "It reads the logs, metrics and recent releases for you, and shows every check it made.",
  },
  {
    title: "You decide",
    body: "You get a likely cause with proof and a confidence level. A fix only runs if you approve it, and the result is checked.",
  },
] as const;

export default function DashboardPage() {
  return (
    <div className="space-y-8">
      <section aria-labelledby="hero-heading" className="max-w-3xl">
        <h1 id="hero-heading" className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Find out why something broke, with proof.
        </h1>
        <p className="mt-3 text-base text-muted">
          AgentOps reads your logs, metrics and recent releases, then tells you the most likely cause, how sure it is,
          and what to do next. When it is not sure, it says so instead of guessing.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <DemoLink />
          <a href="#incidents-heading" className="text-sm underline">
            or pick an incident below
          </a>
        </div>
        <p className="mt-3 text-xs text-muted">
          This demo uses fictional or lab-made incidents. No real systems are touched, and nothing changes without your
          approval.
        </p>
      </section>

      <section aria-labelledby="how-heading">
        <h2 id="how-heading" className="text-sm font-semibold uppercase tracking-wide text-muted">
          How it works
        </h2>
        <ol className="mt-3 grid gap-3 sm:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="rounded-lg border border-border bg-surface p-4">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-fg">
                {i + 1}
              </span>
              <p className="mt-2 font-medium">{s.title}</p>
              <p className="mt-1 text-sm text-muted">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <IncidentDashboard />
    </div>
  );
}
