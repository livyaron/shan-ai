/**
 * The app's startup data in ONE request: GET /lessons/api/_bootstrap returns
 * /me plus the nine tables UserContext used to fetch one by one (nine round
 * trips, 0.2-0.5s each on a phone).
 *
 * Never fatal: a server without the endpoint, a network error or a single table
 * that failed just leaves that part out, and the caller asks for it directly.
 */
export const STARTUP_TABLES = [
  "profiles", "projects", "lessons", "equipment", "notifications",
  "lesson_implementations", "lesson_categories", "feedback", "referent_reviews",
] as const;
export type StartupTable = (typeof STARTUP_TABLES)[number];

export interface Bootstrap {
  /** The session is missing or expired — go to the login page. */
  unauthorized: boolean;
  me: { profile: any; groups?: any[] } | null;
  tables: Partial<Record<StartupTable, any[]>>;
}

const EMPTY: Bootstrap = { unauthorized: false, me: null, tables: {} };

/** Keep only what is well-formed: a known table name holding an array. */
export function readBootstrap(body: unknown): Bootstrap {
  if (!body || typeof body !== "object") return EMPTY;
  const b = body as { me?: any; tables?: Record<string, unknown> };
  const tables: Bootstrap["tables"] = {};
  for (const name of STARTUP_TABLES) {
    const rows = b.tables?.[name];
    if (Array.isArray(rows)) tables[name] = rows;
  }
  const me = b.me && typeof b.me === "object" && b.me.profile ? b.me : null;
  return { unauthorized: false, me, tables };
}

export async function fetchBootstrap(apiUrl: string, doFetch: typeof fetch = fetch): Promise<Bootstrap> {
  try {
    const r = await doFetch(`${apiUrl}/_bootstrap`, { credentials: "same-origin" });
    if (r.status === 401) return { ...EMPTY, unauthorized: true };
    if (!r.ok) return EMPTY;
    return readBootstrap(await r.json());
  } catch {
    return EMPTY;
  }
}
