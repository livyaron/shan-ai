/** yyyy-MM-dd (or an ISO timestamp) → DD/MM/YYYY for people; anything else comes back as is. */
export const ilDate = (value: string | undefined | null): string => {
  if (!value) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : value;
};

/** An ISO timestamp (any zone) → Israel-local DD/MM/YYYY HH:MM; "" when unreadable. */
export const ilDateTime = (value: string | undefined | null): string => {
  if (!value) return "";
  const d = new Date(value);
  if (isNaN(d.getTime())) return "";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Jerusalem", day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(d).map((p) => [p.type, p.value]),
  );
  return `${parts.day}/${parts.month}/${parts.year} ${parts.hour}:${parts.minute}`;
};
