import { Skeleton } from "@/components/ui";

export default function Loading() {
  return (
    <div aria-busy="true" className="space-y-4">
      <Skeleton className="h-40" label="Loading incident" />
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-4">
          <Skeleton className="h-56" />
          <Skeleton className="h-40" />
        </div>
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}
