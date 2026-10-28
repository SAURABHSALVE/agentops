import Link from "next/link";

export default function NotFound() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface p-8 text-center">
      <h1 className="text-lg font-semibold">Incident not found</h1>
      <p className="mt-1 text-sm text-muted">That incident does not exist in the simulated dataset.</p>
      <Link
        href="/"
        className="mt-4 inline-block rounded border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium hover:border-accent"
      >
        Back to incidents
      </Link>
    </div>
  );
}
