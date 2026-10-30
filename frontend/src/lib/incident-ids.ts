// The static export needs the incident pages enumerated at build time. Keep in sync with the backend
// fixtures (a backend test compares this list with them, so drift fails CI).
export const INCIDENT_IDS = ["INC-1001", "INC-1002", "INC-1003", "INC-1004", "INC-1005", "INC-1006"] as const;
