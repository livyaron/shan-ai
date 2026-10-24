// Input contract for the per-project brief. Every line is either a field from
// the master file or a short verbatim fragment of a weekly report — no model
// writes or computes anything here. Real data arrives as --props (never
// committed); the defaults in Root.tsx are demo values and stamp the video.
export type Tone = "bad" | "good" | "missing" | "plain";

export type TimelineEvent = {
  readonly date: string; // DD/MM
  readonly text: string;
  readonly tone: Tone;
};

export type OpenItem = {
  readonly title: string;
  readonly since: string;
  readonly detail: string;
};

export type ProjectBriefData = {
  readonly demo: boolean;
  readonly reportDate: string; // DD/MM/YYYY
  readonly staleNote: string; // "" when the report is current
  readonly id: string;
  readonly name: string;
  readonly subtitle: string;
  readonly stage: string;
  readonly planDate: string; // ISO YYYY-MM-DD
  readonly forecastDate: string; // ISO YYYY-MM-DD
  readonly timeline: readonly TimelineEvent[];
  readonly openItems: readonly OpenItem[];
  readonly flags: readonly string[];
  readonly questions: readonly string[];
};
