/**
 * The default name for a new site in "השלמת נתונים": the station name, read
 * from the project name. The master file writes "<station> - <the work>"
 * ("קרית הגליל-הת' 2 שנאים…", "גבעת אורן - התקנת שנאי…"), so the station is
 * what comes before the first dash; a leading "תחמ"ש"/"תחנת משנה" is the kind
 * of site, not its name. Only a default — the admin edits it before saving. Pure.
 */
const SEPARATOR = /\s*[-–—]\s*/;
const KIND_PREFIX = /^(תחמ["״']?ש|תחנת\s+משנה|תחנת)\s+/;
const MAX = 60;

export const stationNameFrom = (projectName: string | null | undefined): string => {
  const first = (projectName ?? "").split(/\r?\n/)[0].split(SEPARATOR)[0] ?? "";
  return first.replace(KIND_PREFIX, "").replace(/\s+/g, " ").trim().slice(0, MAX);
};

/** An existing site with the same name (case, spaces and gershayim ignored), if any. */
const norm = (s: string) => s.replace(/["״'׳]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
export const findSite = <T extends { label: string }>(sites: T[], name: string): T | undefined =>
  sites.find((s) => norm(s.label) === norm(name));
