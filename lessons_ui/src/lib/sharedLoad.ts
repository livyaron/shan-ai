/**
 * One request shared by every caller that asks within `ttlMs`.
 *
 * A table that renders the same picker on each row used to fire the same two
 * queries once per row: the admin "השלמת נתונים" tab opened with 28 identical
 * `form_field_options` requests in the same instant (2026-11-12). Callers that
 * arrive while the request is in flight, or shortly after, get the same result.
 * A failed request is not kept, so the next caller tries again.
 */
export function sharedLoad<T>(fetcher: () => Promise<T>, ttlMs = 30_000, now: () => number = Date.now) {
  let pending: Promise<T> | null = null;
  let at = 0;

  return {
    load(): Promise<T> {
      if (pending && now() - at < ttlMs) return pending;
      at = now();
      const started = fetcher();
      pending = started;
      started.catch(() => {
        if (pending === started) pending = null;
      });
      return started;
    },
    /** Forget the result — call after writing something the next load must see. */
    invalidate(): void {
      pending = null;
    },
  };
}
