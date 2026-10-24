export const ilDate = (iso: string): string => {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
};

// Days the forecast sits after (+) or before (−) the plan.
export const gapDays = (planIso: string, forecastIso: string): number =>
  Math.round((Date.parse(forecastIso) - Date.parse(planIso)) / 86_400_000);
