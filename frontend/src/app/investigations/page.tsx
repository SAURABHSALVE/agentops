import type { Metadata } from "next";
import { MyInvestigations } from "@/components/MyInvestigations";

export const metadata: Metadata = {
  title: "My investigations · AgentOps AI",
  description: "The investigations you have run, and a way to delete them.",
};

export default function MyInvestigationsPage() {
  return <MyInvestigations />;
}
