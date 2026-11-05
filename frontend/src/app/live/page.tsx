import type { Metadata } from "next";
import { Suspense } from "react";
import { LiveIncident } from "@/components/LiveIncident";
import { Skeleton } from "@/components/ui";

export const metadata: Metadata = {
  title: "Live incident · AgentOps AI",
  description: "Investigate an alarm from your own connected AWS account.",
};

export default function LivePage() {
  // useSearchParams needs a Suspense boundary in a static export.
  return (
    <Suspense fallback={<Skeleton className="h-32" />}>
      <LiveIncident />
    </Suspense>
  );
}
