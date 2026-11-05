import Link from "next/link";
import { Icon } from "@/components/ui";

export default function NotFound() {
  return (
    <div className="card border-dashed p-10 text-center">
      <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-surface-2 text-muted">
        <Icon name="search" />
      </span>
      <h1 className="mt-3 text-lg font-semibold">Incident not found</h1>
      <p className="mt-1 text-sm text-muted">That incident does not exist in this dataset.</p>
      <Link href="/" className="btn btn-secondary mt-4">
        Back to incidents
      </Link>
    </div>
  );
}
