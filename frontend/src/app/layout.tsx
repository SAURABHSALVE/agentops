import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AppHeader } from "@/components/AppHeader";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AgentOps AI",
  description: "Find out why something broke, with proof. An AI incident investigator that shows its evidence.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <AppHeader />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">{children}</main>
        <footer className="border-t border-border">
          <p className="mx-auto w-full max-w-6xl px-4 py-5 text-xs text-muted sm:px-6">
            AgentOps AI is a portfolio project. The AI only proposes: the backend checks every answer, and nothing
            changes without your approval.{" "}
            <a
              href="https://github.com/SAURABHSALVE/agentops"
              className="link"
              target="_blank"
              rel="noreferrer noopener"
            >
              Source on GitHub
            </a>
          </p>
        </footer>
      </body>
    </html>
  );
}
