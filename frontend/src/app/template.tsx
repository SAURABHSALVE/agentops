/** Re-mounted on every navigation, so each page eases in instead of snapping. */
export default function Template({ children }: { children: React.ReactNode }) {
  return <div className="enter">{children}</div>;
}
