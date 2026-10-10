/**
 * Period selection helpers for the admin activity newsletter.
 * Pure, deterministic, Hebrew labels.
 */
export type PeriodKind = "monthly" | "quarterly" | "half" | "yearly";

export interface PeriodOption {
  key: string;
  label: string;
}

export interface PeriodRange {
  start: Date;
  end: Date;
  labelHe: string;
  kind: PeriodKind;
  key: string;
  previous: { start: Date; end: Date; key: string; labelHe: string };
}

const HE_MONTHS = [
  "ינואר","פברואר","מרץ","אפריל","מאי","יוני",
  "יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר",
];

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0);
const endOfDay   = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59, 999);

export const defaultPeriodKey = (kind: PeriodKind, now: Date = new Date()): string => {
  const y = now.getFullYear();
  const m = now.getMonth();
  if (kind === "monthly") {
    const d = new Date(y, m - 1, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }
  if (kind === "quarterly") {
    const currentQ = Math.floor(m / 3) + 1;
    const prevQ = currentQ === 1 ? 4 : currentQ - 1;
    const prevY = currentQ === 1 ? y - 1 : y;
    return `${prevY}-Q${prevQ}`;
  }
  if (kind === "half") {
    const currentH = m < 6 ? 1 : 2;
    const prevH = currentH === 1 ? 2 : 1;
    const prevY = currentH === 1 ? y - 1 : y;
    return `${prevY}-H${prevH}`;
  }
  return `${y - 1}`;
};

export const listPeriods = (kind: PeriodKind, now: Date = new Date()): PeriodOption[] => {
  const y = now.getFullYear();
  const m = now.getMonth();
  const out: PeriodOption[] = [];
  if (kind === "monthly") {
    for (let i = 1; i <= 24; i++) {
      const d = new Date(y, m - i, 1);
      out.push({
        key: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`,
        label: `${HE_MONTHS[d.getMonth()]} ${d.getFullYear()}`,
      });
    }
  } else if (kind === "quarterly") {
    const currentQ = Math.floor(m / 3) + 1;
    for (let i = 1; i <= 8; i++) {
      let q = currentQ - i;
      let yy = y;
      while (q <= 0) { q += 4; yy -= 1; }
      out.push({ key: `${yy}-Q${q}`, label: `רבעון ${q} ${yy}` });
    }
  } else if (kind === "half") {
    const currentH = m < 6 ? 1 : 2;
    for (let i = 1; i <= 6; i++) {
      let h = currentH - i;
      let yy = y;
      while (h <= 0) { h += 2; yy -= 1; }
      out.push({ key: `${yy}-H${h}`, label: `חציון ${h} ${yy}` });
    }
  } else {
    for (let i = 1; i <= 5; i++) {
      const yy = y - i;
      out.push({ key: `${yy}`, label: `שנת ${yy}` });
    }
  }
  return out;
};

export const periodRange = (kind: PeriodKind, key: string): PeriodRange => {
  const buildMonth = (yy: number, mm: number) => ({
    start: startOfDay(new Date(yy, mm, 1)),
    end: endOfDay(new Date(yy, mm + 1, 0)),
    labelHe: `${HE_MONTHS[mm]} ${yy}`,
  });
  const buildQuarter = (yy: number, q: number) => {
    const startM = (q - 1) * 3;
    return {
      start: startOfDay(new Date(yy, startM, 1)),
      end: endOfDay(new Date(yy, startM + 3, 0)),
      labelHe: `רבעון ${q} ${yy}`,
    };
  };
  const buildHalf = (yy: number, h: number) => {
    const startM = h === 1 ? 0 : 6;
    return {
      start: startOfDay(new Date(yy, startM, 1)),
      end: endOfDay(new Date(yy, startM + 6, 0)),
      labelHe: `חציון ${h} ${yy}`,
    };
  };
  const buildYear = (yy: number) => ({
    start: startOfDay(new Date(yy, 0, 1)),
    end: endOfDay(new Date(yy, 11, 31)),
    labelHe: `שנת ${yy}`,
  });

  let cur: { start: Date; end: Date; labelHe: string };
  let prev: { start: Date; end: Date; labelHe: string; key: string };

  if (kind === "monthly") {
    const [yStr, mStr] = key.split("-");
    const yy = Number(yStr), mm = Number(mStr) - 1;
    cur = buildMonth(yy, mm);
    const pYY = mm === 0 ? yy - 1 : yy;
    const pMM = mm === 0 ? 11 : mm - 1;
    prev = { ...buildMonth(pYY, pMM), key: `${pYY}-${String(pMM + 1).padStart(2, "0")}` };
  } else if (kind === "quarterly") {
    const [yStr, qStr] = key.split("-Q");
    const yy = Number(yStr), q = Number(qStr);
    cur = buildQuarter(yy, q);
    const pYY = q === 1 ? yy - 1 : yy;
    const pQ = q === 1 ? 4 : q - 1;
    prev = { ...buildQuarter(pYY, pQ), key: `${pYY}-Q${pQ}` };
  } else if (kind === "half") {
    const [yStr, hStr] = key.split("-H");
    const yy = Number(yStr), h = Number(hStr);
    cur = buildHalf(yy, h);
    const pYY = h === 1 ? yy - 1 : yy;
    const pH = h === 1 ? 2 : 1;
    prev = { ...buildHalf(pYY, pH), key: `${pYY}-H${pH}` };
  } else {
    const yy = Number(key);
    cur = buildYear(yy);
    prev = { ...buildYear(yy - 1), key: `${yy - 1}` };
  }

  return { ...cur, kind, key, previous: prev };
};

export const formatDateRange = (start: Date, end: Date): string => {
  const fmt = (d: Date) =>
    `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
  return `${fmt(start)} – ${fmt(end)}`;
};

export const periodKindLabel: Record<PeriodKind, string> = {
  monthly: "חודשי",
  quarterly: "רבעוני",
  half: "חצי שנתי",
  yearly: "שנתי",
};
