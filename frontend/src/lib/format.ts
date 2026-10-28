// Deterministic ISO-string slicing (no Date/Intl) so server and client render identically.

/** "2026-10-28T09:12:00Z" -> "28 Oct 2026" */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
}

/** "2026-10-28T09:12:00Z" -> "09:12:00 UTC" */
export function formatTime(iso: string): string {
  return `${iso.slice(11, 19)} UTC`;
}

/** "2026-10-28T09:12:00Z" -> "28 Oct 2026, 09:12 UTC" */
export function formatDateTime(iso: string): string {
  return `${formatDate(iso)}, ${iso.slice(11, 16)} UTC`;
}

/** Window like "09:12–09:42 UTC" (assumes same day, true for all seeded incidents). */
export function formatWindow(startIso: string, endIso: string): string {
  return `${startIso.slice(11, 16)}–${endIso.slice(11, 16)} UTC`;
}
