// The ONLY input contract. Real numbers come from a props file at render
// time (`--props=data.json`, never committed — the master file is inside
// information). The defaults in Root.tsx are demo values and render with a
// "נתוני דוגמה" stamp so they can never pass for a real report.
export type Sector = {
  readonly label: string;
  readonly count: number;
};

export type WeeklyData = {
  readonly demo: boolean;
  readonly reportDate: string; // DD/MM/YYYY, as read by a person
  readonly active: number;
  readonly finished: number;
  readonly sectors: readonly Sector[];
  readonly triage: {
    readonly escalate: number;
    readonly thisWeek: number;
    readonly followUp: number;
    readonly fyi: number;
  };
  readonly forecastMoved: number;
  readonly pastDue: number;
  readonly stuck: number;
};
