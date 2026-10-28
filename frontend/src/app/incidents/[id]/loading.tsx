export default function Loading() {
  return (
    <div role="status" aria-label="Loading incident" className="space-y-4">
      <div className="h-32 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-4">
          <div className="h-40 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
          <div className="h-56 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
        </div>
        <div className="h-64 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
      </div>
      <span className="sr-only">Loading incident…</span>
    </div>
  );
}
