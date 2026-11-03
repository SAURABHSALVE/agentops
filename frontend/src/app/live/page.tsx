import type { Metadata } from "next";
import { Suspense } from "react";
import { LiveIncident } from "@/components/LiveIncident";

export const metadata: Metadata = {
  title: "Live incident · AgentOps AI",
  description: "Investigate an alarm from your own connected AWS account.",
};

export default function LivePage() {
  // useSearchParams needs a Suspense boundary in a static export.
  return (
    <Suspense fallback={<div className="h-32 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />}>
      <LiveIncident />
    </Suspense>
  );
}
