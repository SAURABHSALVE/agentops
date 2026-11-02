import type { Metadata } from "next";
import { ConnectAws } from "@/components/ConnectAws";

export const metadata: Metadata = {
  title: "Connect your AWS account · AgentOps AI",
  description: "Give AgentOps read-only access to your own alarms, metrics and logs.",
};

export default function ConnectPage() {
  return <ConnectAws />;
}
