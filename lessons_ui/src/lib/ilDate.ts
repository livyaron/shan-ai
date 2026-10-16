/** yyyy-MM-dd (or an ISO timestamp) → DD/MM/YYYY for people; anything else comes back as is. */
export const ilDate = (value: string | undefined | null): string => {
  if (!value) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : value;
};
