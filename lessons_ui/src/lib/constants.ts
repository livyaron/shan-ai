// Centralized UI constants used across multiple pages

export const riskColors: Record<string, string> = {
  high: "bg-destructive/10 text-destructive border-destructive/20",
  medium: "bg-warning/10 text-warning border-warning/20",
  low: "bg-success/10 text-success border-success/20",
};

export const riskLabels: Record<string, string> = {
  high: "סיכון גבוה",
  medium: "סיכון בינוני",
  low: "סיכון נמוך",
};

export const riskLabelsShort: Record<string, string> = {
  high: "גבוה",
  medium: "בינוני",
  low: "נמוך",
};

export const statusColors: Record<string, string> = {
  new: "bg-info/10 text-info border-info/20",
  approved: "bg-success/10 text-success border-success/20",
  rejected: "bg-destructive/10 text-destructive border-destructive/20",
  distributed: "bg-primary/10 text-primary border-primary/20",
  closed: "bg-muted text-muted-foreground border-muted-foreground/20",
};

export const statusLabels: Record<string, string> = {
  new: "ממתין לאישור",
  approved: "מאושר",
  rejected: "נדחה",
  distributed: "הופץ",
  closed: "סגור",
};

export const statusLabelsShort: Record<string, string> = {
  new: "ממתין",
  approved: "מאושר",
  rejected: "נדחה",
  distributed: "הופץ",
  closed: "סגור",
};

export const priorityLabels: Record<string, string> = {
  high: "עדיפות גבוהה",
  medium: "עדיפות בינונית",
  low: "עדיפות נמוכה",
};

export const feedbackTypeColors: Record<string, string> = {
  change: "bg-info/10 text-info border-info/20",
  add: "bg-success/10 text-success border-success/20",
  fix: "bg-destructive/10 text-destructive border-destructive/20",
  general: "bg-muted text-muted-foreground border-muted-foreground/20",
};

export const feedbackStatusColors: Record<string, string> = {
  open: "bg-info/10 text-info border-info/20",
  in_progress: "bg-warning/10 text-warning border-warning/20",
  closed: "bg-success/10 text-success border-success/20",
};

export const PROFESSIONAL_DOMAINS = [
  "תכנון אזרחי",
  "תכנון אלקטרומכני",
  "תכנון חשמלי",
  "רכש",
  "פיקוח אלקטרומכני",
  "פיקוח אזרחי",
  "הרכבה חשמלית",
  "בדיקות הכנסה לניצול",
  "אחר",
];

export const WORKFLOW_STATUSES = [
  "חדש",
  "בטיפול רפרנט",
  "ממתין להשלמת יוצר",
  "ממתין לאישור מנהל מערכת",
  "אושר והופץ",
  "נדחה",
];

// Active workflow statuses shown on the Kanban board
export const ACTIVE_WORKFLOW_STATUSES = [
  "חדש",
  "בטיפול רפרנט",
  "ממתין להשלמת יוצר",
  "ממתין לאישור מנהל מערכת",
];

// Final workflow statuses (not shown on board)
export const FINAL_WORKFLOW_STATUSES = [
  "אושר והופץ",
  "נדחה",
];

export const workflowStatusColors: Record<string, string> = {
  "חדש": "bg-info/10 text-info border-info/20",
  "בטיפול רפרנט": "bg-warning/10 text-warning border-warning/20",
  "ממתין להשלמת יוצר": "bg-accent/10 text-accent border-accent/20",
  "ממתין לאישור מנהל מערכת": "bg-primary/10 text-primary border-primary/20",
  "אושר והופץ": "bg-success/10 text-success border-success/20",
  "נדחה": "bg-destructive/10 text-destructive border-destructive/20",
};

export const REFERENT_DECISIONS = [
  "הלקח יושם",
  "בטיפול",
  "יוקם צוות לבחינה",
  "נדרש מידע נוסף",
  "לא רלוונטי",
  "הלקח נדחה",
];

export const referentDecisionColors: Record<string, string> = {
  "הלקח יושם": "bg-success/10 text-success border-success/20",
  "בטיפול": "bg-warning/10 text-warning border-warning/20",
  "יוקם צוות לבחינה": "bg-primary/10 text-primary border-primary/20",
  "נדרש מידע נוסף": "bg-accent/10 text-accent border-accent/20",
  "לא רלוונטי": "bg-muted text-muted-foreground border-muted-foreground/20",
  "הלקח נדחה": "bg-destructive/10 text-destructive border-destructive/20",
};

// Return reasons for returning a lesson to its creator
export const RETURN_REASONS = [
  "חסר מידע",
  "ניסוח לא ברור",
  "חסר קובץ",
  "שיוך לא נכון",
  "אחר",
];

// SLA thresholds (in days)
export const SLA_WARNING_DAYS = 7;
export const SLA_OVERDUE_DAYS = 10;

// SLA status labels
export const SLA_STATUS = {
  OK: "תקין",
  WARNING: "מתקרב לחריגה",
  OVERDUE: "באיחור",
} as const;

export type SlaStatus = typeof SLA_STATUS[keyof typeof SLA_STATUS];

// Calculate SLA status from referent start date
export const calculateSlaStatus = (referentStartDate: string | undefined): { status: SlaStatus; days: number } => {
  if (!referentStartDate) return { status: SLA_STATUS.OK, days: 0 };
  const startDate = new Date(referentStartDate);
  const now = new Date();
  const daysDiff = Math.floor((now.getTime() - startDate.getTime()) / (1000 * 60 * 60 * 24));
  if (daysDiff >= SLA_OVERDUE_DAYS) return { status: SLA_STATUS.OVERDUE, days: daysDiff };
  if (daysDiff >= SLA_WARNING_DAYS) return { status: SLA_STATUS.WARNING, days: daysDiff };
  return { status: SLA_STATUS.OK, days: daysDiff };
};

export const slaStatusColors: Record<SlaStatus, string> = {
  [SLA_STATUS.OK]: "bg-success/10 text-success border-success/20",
  [SLA_STATUS.WARNING]: "bg-warning/10 text-warning border-warning/20",
  [SLA_STATUS.OVERDUE]: "bg-destructive/10 text-destructive border-destructive/20",
};
