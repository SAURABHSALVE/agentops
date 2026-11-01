// The static export needs the incident pages enumerated at build time. Keep in sync with the backend: the
// simulated fixtures (INC-1xxx) then the captured lab dataset (INC-2xxx). A backend test compares this list
// with both, so drift fails CI. The backend serves one set or the other (TELEMETRY_DATASET).
export const INCIDENT_IDS = [
  "INC-1001",
  "INC-1002",
  "INC-1003",
  "INC-1004",
  "INC-1005",
  "INC-1006",
  "INC-2001",
  "INC-2002",
  "INC-2003",
] as const;
