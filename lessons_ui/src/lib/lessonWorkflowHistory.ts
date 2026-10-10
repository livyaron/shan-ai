/**
 * Lesson workflow history: append-only trail of every meaningful workflow event.
 * Actor name/role are stored as a snapshot at event time, so later profile changes
 * never rewrite history.
 */

export const WORKFLOW_EVENT_TYPES = {
  CREATED: "lesson_created",
  ASSIGNED_REFERENT: "assigned_to_referent",
  REFERENT_RESPONSE: "referent_response",
  RETURNED_TO_CREATOR: "returned_to_creator",
  CREATOR_COMPLETED: "creator_completed",
  SENT_TO_ADMIN: "sent_to_admin",
  ADMIN_RESPONSE: "admin_response",
  APPROVED: "approved",
  REJECTED: "rejected",
  DISTRIBUTED: "distributed",
  CLOSED: "closed",
  STATUS_CHANGE: "status_change",
} as const;

export type WorkflowEventType = (typeof WORKFLOW_EVENT_TYPES)[keyof typeof WORKFLOW_EVENT_TYPES];

export const workflowEventLabels: Record<string, string> = {
  [WORKFLOW_EVENT_TYPES.CREATED]: "יצירת לקח",
  [WORKFLOW_EVENT_TYPES.ASSIGNED_REFERENT]: "שיוך לרפרנט",
  [WORKFLOW_EVENT_TYPES.REFERENT_RESPONSE]: "התייחסות רפרנט",
  [WORKFLOW_EVENT_TYPES.RETURNED_TO_CREATOR]: "החזרה להשלמת יוצר",
  [WORKFLOW_EVENT_TYPES.CREATOR_COMPLETED]: "השלמת מידע ע\"י היוצר",
  [WORKFLOW_EVENT_TYPES.SENT_TO_ADMIN]: "העברה לאישור מנהל מערכת",
  [WORKFLOW_EVENT_TYPES.ADMIN_RESPONSE]: "התייחסות מנהל מערכת",
  [WORKFLOW_EVENT_TYPES.APPROVED]: "אישור הלקח",
  [WORKFLOW_EVENT_TYPES.REJECTED]: "דחיית הלקח",
  [WORKFLOW_EVENT_TYPES.DISTRIBUTED]: "הפצת הלקח",
  [WORKFLOW_EVENT_TYPES.CLOSED]: "סגירת הלקח",
  [WORKFLOW_EVENT_TYPES.STATUS_CHANGE]: "שינוי סטטוס",
};

/** Visual accent per actor role, using semantic design tokens only */
export const workflowActorRoleStyles: Record<string, { dot: string; badge: string }> = {
  admin: { dot: "bg-primary", badge: "bg-primary/10 text-primary border-primary/20" },
  project_manager: { dot: "bg-success", badge: "bg-success/10 text-success border-success/20" },
  referent: { dot: "bg-warning", badge: "bg-warning/10 text-warning border-warning/20" },
  viewer: { dot: "bg-muted-foreground", badge: "bg-muted text-muted-foreground border-border" },
};

export const getWorkflowActorRoleStyle = (role: string) =>
  workflowActorRoleStyles[role] ?? workflowActorRoleStyles.viewer;

export interface LessonWorkflowEvent {
  id: number;
  lessonId: number;
  eventType: string;
  statusBefore?: string | null;
  statusAfter?: string | null;
  actorId: string;
  actorName: string;
  actorRole: string;
  targetUserId?: string | null;
  targetUserName?: string | null;
  note?: string | null;
  createdAt: string;
}

export const mapWorkflowEventRow = (row: any): LessonWorkflowEvent => ({
  id: row.id,
  lessonId: row.lesson_id,
  eventType: row.event_type,
  statusBefore: row.status_before,
  statusAfter: row.status_after,
  actorId: row.actor_id,
  actorName: row.actor_name,
  actorRole: row.actor_role,
  targetUserId: row.target_user_id,
  targetUserName: row.target_user_name,
  note: row.note,
  createdAt: row.created_at,
});

/** DD/MM/YYYY HH:mm */
export const formatWorkflowEventDateTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");

  return `${day}/${month}/${date.getFullYear()} ${hours}:${minutes}`;
};

/** Derive the workflow event type from a target workflow status */
export const eventTypeForStatus = (status: string): string => {
  switch (status) {
    case "בטיפול רפרנט":
      return WORKFLOW_EVENT_TYPES.ASSIGNED_REFERENT;
    case "ממתין להשלמת יוצר":
      return WORKFLOW_EVENT_TYPES.RETURNED_TO_CREATOR;
    case "ממתין לאישור מנהל מערכת":
      return WORKFLOW_EVENT_TYPES.SENT_TO_ADMIN;
    case "אושר והופץ":
      return WORKFLOW_EVENT_TYPES.APPROVED;
    case "נדחה":
      return WORKFLOW_EVENT_TYPES.REJECTED;
    case "נסגר":
      return WORKFLOW_EVENT_TYPES.CLOSED;
    default:
      return WORKFLOW_EVENT_TYPES.STATUS_CHANGE;
  }
};
