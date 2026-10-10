import { createContext, useContext, useState, useEffect, ReactNode, useCallback, useRef } from "react";
import { supabase, SUPABASE_URL, SHAN_LOGIN_URL } from "@/integrations/supabase/client";
import {
  LessonWorkflowEvent,
  WORKFLOW_EVENT_TYPES,
  eventTypeForStatus,
  mapWorkflowEventRow,
} from "@/lib/lessonWorkflowHistory";


export type UserRole = "admin" | "project_manager" | "referent" | "viewer";

export interface EmailPreferences {
  new_lesson: boolean;
  approved: boolean;
  rejected: boolean;
  distributed: boolean;
  stage_change: boolean;
  ai_suggestion: boolean;
}

export const defaultEmailPreferences: EmailPreferences = {
  new_lesson: true,
  approved: true,
  rejected: true,
  distributed: true,
  stage_change: true,
  ai_suggestion: true,
};

export const emailPreferenceLabels: Record<keyof EmailPreferences, string> = {
  new_lesson: "לקח חדש ממתין לאישור",
  approved: "אישור לקח",
  rejected: "דחיית לקח",
  distributed: "הפצת לקח לפרויקט",
  stage_change: "שינוי בפרויקט",
  ai_suggestion: "הצעות AI ללקחים רלוונטיים",
};

export interface MockUser {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  assignedProjects: number[];
  assignedEquipmentIds: number[];
  assignedStageIndexes: number[];
  emailPreferences: EmailPreferences;
}

export const mockUsers: MockUser[] = [
  { id: "u1", name: "יוסי כהן", email: "yossi@org.com", role: "admin", assignedProjects: [1, 2, 3, 4], assignedEquipmentIds: [], assignedStageIndexes: [], emailPreferences: defaultEmailPreferences },
  { id: "u2", name: "שרה לוי", email: "sara@org.com", role: "project_manager", assignedProjects: [1, 2], assignedEquipmentIds: [], assignedStageIndexes: [], emailPreferences: defaultEmailPreferences },
  { id: "u3", name: "רון אביב", email: "ron@org.com", role: "project_manager", assignedProjects: [3, 4], assignedEquipmentIds: [], assignedStageIndexes: [], emailPreferences: defaultEmailPreferences },
];

export const roleLabels: Record<UserRole, string> = {
  admin: "מנהל מערכת",
  project_manager: "מנהל פרויקט",
  referent: "רפרנט",
  viewer: "צפייה בלבד",
};

export const PROJECT_STAGES = [
  "הקפאת תכולה",
  "הקפאת תצורה",
  "תכנון",
  "קבלת היתר",
  "בחירת קבלן",
  "עבודות אזרחיות",
  "הרכבות חשמליות",
  "בדיקות",
  "טופס 4",
  "הסתיים",
  "חישמול",
];

export type LessonStatus = "new" | "approved" | "rejected" | "distributed" | "closed";

export type ProjectType = "new_build" | "expansion" | "maintenance";
export type StationType = "closed" | "open" | "switching" | "combined" | "mobile_substation";

export const projectTypeLabels: Record<ProjectType, string> = {
  new_build: "הקמה",
  expansion: "הרחבה",
  maintenance: "שו\"ש",
};

export const stationTypeLabels: Record<StationType, string> = {
  closed: "תחנה סגורה",
  open: "תחנה פתוחה",
  switching: "תחנת מיתוג",
  combined: "תחנה משולבת",
  mobile_substation: "תחנת משנה ניידת",
};

export interface Project {
  id: number;
  name: string;
  stageIndex: number; // derived = MAX(stageIndexes), kept for backward compat (dashboards/KPIs/timeline)
  stageIndexes: number[]; // multi-select active stages, min 1
  lessonsCount: number;
  risk: string;
  equipmentIds: number[];
  projectType: ProjectType;
  stationType: StationType;
  managerId: string;
  site?: string;
}

export interface LessonAIReview {
  recommended_projects: { project_name: string; reason: string; priority: string }[];
  recommended_referents: { referent_id: string; referent_name: string; reason: string }[];
  implementation_steps: string[];
  attention_points: string[];
  summary: string;
}

export interface ProjectAnalysis {
  summary: string;
  strengths: string[];
  weaknesses: string[];
  recommendations: string[];
}

export interface RelevantLessonSuggestion {
  lessonId: number;
  reason: string;
  priority: "high" | "medium" | "low";
}

export interface Lesson {
  id: number;
  title: string;
  description: string;
  recommendation: string;
  project: string;
  projectId: number | null;
  stage: string;
  category: string;
  risk: "high" | "medium" | "low";
  status: LessonStatus;
  date: string;
  createdBy: string;
  approvedBy?: string;
  distributedTo?: number[];
  distributedToReferents?: string[];
  equipmentIds: number[];
  aiReview?: LessonAIReview;
  professionalDomain?: string;
  eventDate?: string;
  assignedTo?: string;
  workflowStatus: string;
  impactScheduleDelay?: number;
  impactBudgetCost?: number;
  impactQualityDesc?: string;
  fileUrl?: string;
  referentStartDate?: string;
  targetDate?: string;
  closedDate?: string;
  updatedAt?: string;
  returnReason?: string;
  returnDate?: string;
  returnedBy?: string;
}

export interface ReferentReview {
  id: number;
  lessonId: number;
  referentId: string;
  reason: string;
  priority: "high" | "medium" | "low";
  isRelevant?: boolean;
  isImplemented?: boolean;
  notes?: string;
  respondedAt?: string;
  decision?: string;
  responseText?: string;
}

export interface Equipment {
  id: number;
  name: string;
  category: string;
}

export interface LessonImplementation {
  id: number;
  lessonId: number;
  projectId: number;
  reason: string;
  priority: "high" | "medium" | "low";
  isRelevant?: boolean;
  isImplemented?: boolean;
  notes?: string;
  respondedBy?: string;
  respondedAt?: string;
}

export interface Notification {
  id: number;
  userId: string;
  type: "new_lesson" | "approved" | "rejected" | "distributed" | "stage_change" | "ai_suggestion";
  title: string;
  description: string;
  time: string;
  read: boolean;
  entityType?: "lesson" | "project" | "feedback";
  entityId?: string;
}

export interface Feedback {
  id: number;
  userId: string;
  userName: string;
  type: "change" | "add" | "fix" | "general";
  title: string;
  description: string;
  status: "open" | "in_progress" | "closed";
  adminNotes: string;
  createdAt: string;
  closedAt?: string;
}

export const feedbackTypeLabels: Record<string, string> = {
  change: "שינוי",
  add: "הוספה",
  fix: "תיקון",
  general: "כללי",
};

export const feedbackStatusLabels: Record<string, string> = {
  open: "פתוח",
  in_progress: "בטיפול",
  closed: "סגור",
};

interface ActivityActor {
  id: string;
  name: string;
  email: string;
  role: UserRole;
}

interface ActivityLogPayload {
  actionType: string;
  entityType: string;
  entityId?: string | number | null;
  objectLabel: string;
  description: string;
  actor?: ActivityActor;
}

const mapDbFeedback = (row: any): Feedback => ({
  id: row.id,
  userId: row.user_id,
  userName: row.user_name,
  type: row.type,
  title: row.title,
  description: row.description,
  status: row.status,
  adminNotes: row.admin_notes || "",
  createdAt: row.created_at,
  closedAt: row.closed_at || undefined,
});

interface AppContextType {
  currentUser: MockUser;
  setCurrentUser: (user: MockUser) => void;
  isLoggedIn: boolean;
  /** Who the Shan-AI session may act as: the user themself + their referent groups. */
  identities: MockUser[];
  switchIdentity: (id: string) => void;
  logout: () => void;
  hasPermission: (permission: string) => boolean;
  users: MockUser[];
  updateUser: (userId: string, updates: Partial<Pick<MockUser, "name" | "email" | "role" | "assignedProjects" | "assignedEquipmentIds" | "assignedStageIndexes">>) => void;
  addUser: (user: Omit<MockUser, "id">) => Promise<void>;
  deleteUser: (userId: string) => Promise<void>;
  updateEmailPreferences: (userId: string, prefs: EmailPreferences) => Promise<void>;
  projects: Project[];
  addProject: (project: Omit<Project, "id" | "lessonsCount">) => void;
  deleteProject: (projectId: number) => Promise<void>;
  updateProject: (projectId: number, updates: Partial<Pick<Project, "name" | "risk" | "projectType" | "stationType" | "managerId" | "site">>) => void;
  updateProjectStage: (projectId: number, newStageIndex: number) => Promise<void>;
  updateProjectStages: (projectId: number, newStageIndexes: number[]) => Promise<void>;
  updateProjectEquipment: (projectId: number, equipmentIds: number[]) => void;
  equipment: Equipment[];
  addEquipment: (eq: Omit<Equipment, "id">, projectIds?: number[]) => Promise<Equipment | undefined>;
  updateEquipment: (eqId: number, updates: Partial<Pick<Equipment, "name" | "category">>) => Promise<void>;
  removeEquipment: (eqId: number) => void;
  renameCategory: (oldName: string, newName: string) => Promise<void>;
  deleteCategory: (categoryName: string) => Promise<void>;
  lessons: Lesson[];
  addLesson: (lesson: Omit<Lesson, "id" | "date" | "status" | "createdBy" | "workflowStatus">) => void;
  updateWorkflowStatus: (lessonId: number, status: string, note?: string, eventType?: string) => Promise<void>;
  returnLessonToCreator: (lessonId: number, reason: string, customReason?: string) => Promise<void>;
  updateReferentDecision: (reviewId: number, decision: string, responseText: string) => Promise<void>;
  fetchLessonWorkflowEvents: (lessonId: number) => Promise<LessonWorkflowEvent[]>;
  workflowEventsVersion: number;

  approveLesson: (lessonId: number) => void;
  rejectLesson: (lessonId: number) => void;
  closeLesson: (lessonId: number) => void;
  distributeLesson: (lessonId: number, targetProjectIds?: number[], targetReferentIds?: string[], distributionSource?: "manual" | "ai") => void;
  updateLessonAIReview: (lessonId: number, aiReview: LessonAIReview) => void;
  updateLesson: (lessonId: number, updates: Partial<Pick<Lesson, "title" | "description" | "recommendation" | "stage" | "category" | "risk" | "equipmentIds" | "professionalDomain" | "eventDate" | "assignedTo" | "workflowStatus" | "impactScheduleDelay" | "impactBudgetCost" | "impactQualityDesc" | "fileUrl" | "referentStartDate" | "targetDate" | "closedDate">>) => Promise<void>;
  deleteLesson: (lessonId: number) => void;
  removeLessonFromProject: (lessonId: number, projectId: number) => Promise<void>;
  notifications: Notification[];
  unreadCount: number;
  markNotificationsAsRead: () => Promise<void>;
  markNotificationsAsReadByIds: (ids: number[]) => Promise<void>;
  markSingleNotificationAsRead: (id: number) => Promise<void>;
  clearNotifications: () => Promise<void>;
  implementations: LessonImplementation[];
  referentReviews: ReferentReview[];
  analyzeRelevantLessons: (projectId: number) => Promise<ProjectAnalysis | null>;
  fetchRelevantLessonSuggestions: (projectId: number, stageIndexOverride?: number) => Promise<{ projectAnalysis: ProjectAnalysis | null; suggestions: RelevantLessonSuggestion[] }>;
  addSuggestedLessonsToProject: (projectId: number, suggestions: RelevantLessonSuggestion[]) => Promise<number>;
  respondToImplementation: (implId: number, response: { isRelevant: boolean; isImplemented: boolean; notes: string }) => void;
  respondToReferentReview: (reviewId: number, response: { isRelevant: boolean; isImplemented: boolean; notes: string }) => void;
  isAnalyzing: boolean;
  isLoading: boolean;
  lessonCategories: string[];
  addLessonCategory: (name: string) => Promise<void>;
  renameLessonCategory: (oldName: string, newName: string) => Promise<void>;
  deleteLessonCategory: (name: string) => Promise<void>;
  feedbacks: Feedback[];
  addFeedback: (feedback: { type: string; title: string; description: string }) => Promise<void>;
  updateFeedbackStatus: (id: number, status: string, adminNotes?: string) => Promise<void>;
  deleteFeedback: (id: number) => Promise<void>;
}

const AppContext = createContext<AppContextType | null>(null);

const permissions: Record<UserRole, string[]> = {
  admin: ["manage_users", "manage_projects", "view_all_lessons", "manage_roles", "approve_lessons", "add_lessons", "distribute_lessons", "change_stage"],
  project_manager: ["manage_own_projects", "add_lessons", "view_project_lessons", "change_stage"],
  referent: ["view_project_lessons", "add_lessons"],
  viewer: ["view_all_lessons"],
};

// Helper to map DB row to app model
const mapDbProject = (row: any): Project => {
  const stageIndexes: number[] = Array.isArray(row.stage_indexes) && row.stage_indexes.length > 0
    ? row.stage_indexes
    : [row.stage_index ?? 0];
  return {
    id: row.id,
    name: row.name,
    stageIndex: row.stage_index ?? Math.max(...stageIndexes),
    stageIndexes,
    lessonsCount: row.lessons_count,
    risk: row.risk,
    equipmentIds: row.equipment_ids || [],
    projectType: row.project_type as ProjectType,
    stationType: row.station_type as StationType,
    managerId: row.manager_id,
    site: row.site || undefined,
  };
};

const mapDbLesson = (row: any): Lesson => ({
  id: row.id,
  title: row.title,
  description: row.description,
  recommendation: row.recommendation || "",
  project: row.project_name,
  projectId: row.project_id ?? null,
  stage: row.stage,
  category: row.category,
  risk: row.risk,
  status: row.status as LessonStatus,
  date: row.date,
  createdBy: row.created_by,
  approvedBy: row.approved_by || undefined,
  distributedTo: row.distributed_to || undefined,
  distributedToReferents: row.distributed_to_referents || undefined,
  equipmentIds: row.equipment_ids || [],
  aiReview: row.ai_review || undefined,
  professionalDomain: row.professional_domain || undefined,
  eventDate: row.event_date || undefined,
  assignedTo: row.assigned_to || undefined,
  workflowStatus: row.workflow_status || "חדש",
  impactScheduleDelay: row.impact_schedule_delay ?? undefined,
  impactBudgetCost: row.impact_budget_cost ?? undefined,
  impactQualityDesc: row.impact_quality_desc || undefined,
  fileUrl: row.file_url || undefined,
  referentStartDate: row.referent_start_date || undefined,
  targetDate: row.target_date || undefined,
  closedDate: row.closed_date || undefined,
  updatedAt: row.updated_at || undefined,
  returnReason: row.return_reason || undefined,
  returnDate: row.return_date || undefined,
  returnedBy: row.returned_by || undefined,
});

const mapDbUser = (row: any): MockUser => ({
  id: row.id,
  name: row.name,
  email: row.email,
  role: row.role as UserRole,
  assignedProjects: row.assigned_projects || [],
  assignedEquipmentIds: row.assigned_equipment_ids || [],
  assignedStageIndexes: row.assigned_stage_indexes || [],
  emailPreferences: row.email_preferences || defaultEmailPreferences,
});

const ACT_AS_KEY = "lessons_act_as";

/** GET /lessons/api/me — null when there is no Shan-AI session. */
const fetchMe = async (): Promise<{ self: MockUser; groups: MockUser[] } | null> => {
  const r = await fetch(`${SUPABASE_URL}/me`, { credentials: "same-origin" });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`/me failed: ${r.status}`);
  const body = await r.json();
  return { self: mapDbUser(body.profile), groups: (body.groups || []).map(mapDbUser) };
};

const mapDbReferentReview = (row: any): ReferentReview => ({
  id: row.id,
  lessonId: row.lesson_id,
  referentId: row.referent_id,
  reason: row.reason,
  priority: row.priority as "high" | "medium" | "low",
  isRelevant: row.is_relevant ?? undefined,
  isImplemented: row.is_implemented ?? undefined,
  notes: row.notes ?? undefined,
  respondedAt: row.responded_at ?? undefined,
  decision: row.decision ?? undefined,
  responseText: row.response_text ?? undefined,
});

const mapDbNotification = (row: any): Notification => ({
  id: row.id,
  userId: row.user_id,
  type: row.type as Notification["type"],
  title: row.title,
  description: row.description,
  time: row.time,
  read: row.read,
  entityType: row.entity_type || undefined,
  entityId: row.entity_id || undefined,
});

const mapDbImpl = (row: any): LessonImplementation => ({
  id: row.id,
  lessonId: row.lesson_id,
  projectId: row.project_id,
  reason: row.reason,
  priority: row.priority as "high" | "medium" | "low",
  isRelevant: row.is_relevant ?? undefined,
  isImplemented: row.is_implemented ?? undefined,
  notes: row.notes ?? undefined,
  respondedBy: row.responded_by ?? undefined,
  respondedAt: row.responded_at ?? undefined,
});

export const UserProvider = ({ children }: { children: ReactNode }) => {
  const [users, setUsers] = useState<MockUser[]>(mockUsers);
  const [currentUser, setCurrentUser] = useState<MockUser>(mockUsers[0]);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [identities, setIdentities] = useState<MockUser[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [implementations, setImplementations] = useState<LessonImplementation[]>([]);
  const [referentReviews, setReferentReviews] = useState<ReferentReview[]>([]);
  const [equipment, setEquipment] = useState<Equipment[]>([]);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [lessonCategories, setLessonCategories] = useState<string[]>([]);
  const [feedbacks, setFeedbacks] = useState<Feedback[]>([]);
  // Bumped whenever a workflow event is appended, so open history views refresh
  const [workflowEventsVersion, setWorkflowEventsVersion] = useState(0);

  /** Read the append-only workflow history of a single lesson (chronological) */
  const fetchLessonWorkflowEvents = useCallback(async (lessonId: number): Promise<LessonWorkflowEvent[]> => {
    const { data, error } = await supabase
      .from("lesson_workflow_events" as any)
      .select("*")
      .eq("lesson_id", lessonId)
      .order("created_at", { ascending: true })
      .order("id", { ascending: true });

    if (error) {
      console.error("Failed to load lesson workflow history:", error);
      return [];
    }

    return ((data as any[]) || []).map(mapWorkflowEventRow);
  }, []);

  /** Append a history event without changing the lesson status (comments / responses) */
  const recordWorkflowEvent = useCallback(async (params: {
    lessonId: number;
    eventType: string;
    statusAfter?: string | null;
    note?: string | null;
    targetUserId?: string | null;
    targetUserName?: string | null;
  }) => {
    // Read-only users may never write workflow history
    if (currentUser.role === "viewer") return;

    const { error } = await supabase.from("lesson_workflow_events" as any).insert({
      lesson_id: params.lessonId,
      event_type: params.eventType,
      status_before: params.statusAfter ?? null,
      status_after: params.statusAfter ?? null,
      actor_id: currentUser.id,
      actor_name: currentUser.name,
      actor_role: currentUser.role,
      target_user_id: params.targetUserId ?? null,
      target_user_name: params.targetUserName ?? null,
      note: params.note ?? null,
    });

    if (error) {
      console.error("Failed to record workflow event:", error);
      return;
    }

    setWorkflowEventsVersion((v) => v + 1);
  }, [currentUser]);

  /**
   * Atomic workflow transition: the lesson update and the history event are written
   * inside a single database call, so a status can never change without a history entry.
   */
  const applyWorkflowTransition = useCallback(async (params: {
    lessonId: number;
    eventType: string;
    statusAfter: string;
    lessonUpdates: Record<string, unknown>;
    note?: string | null;
    targetUserId?: string | null;
    targetUserName?: string | null;
  }) => {
    if (currentUser.role === "viewer") {
      throw new Error("למשתמש צפייה בלבד אין הרשאה לשנות סטטוס לקח");
    }

    const { error } = await supabase.rpc("apply_lesson_workflow_event" as any, {
      p_lesson_id: params.lessonId,
      p_event_type: params.eventType,
      p_status_after: params.statusAfter,
      p_actor_id: currentUser.id,
      p_actor_name: currentUser.name,
      p_actor_role: currentUser.role,
      p_lesson_updates: params.lessonUpdates,
      p_note: params.note ?? null,
      p_target_user_id: params.targetUserId ?? null,
      p_target_user_name: params.targetUserName ?? null,
    });

    if (error) throw error;

    setWorkflowEventsVersion((v) => v + 1);
  }, [currentUser]);



  const logActivity = useCallback(async ({ actionType, entityType, entityId, objectLabel, description, actor }: ActivityLogPayload) => {
    const activityActor = actor ?? currentUser;

    if (!activityActor?.id) {
      return;
    }

    const { error } = await supabase.from("activity_logs" as any).insert({
      user_id: activityActor.id,
      user_name: activityActor.name,
      user_email: activityActor.email,
      user_role: activityActor.role,
      action_type: actionType,
      entity_type: entityType,
      entity_id: entityId ? String(entityId) : null,
      object_label: objectLabel,
      description,
    });

    if (error) {
      console.error("Failed to write activity log:", error);
    }
  }, [currentUser]);

  const describeChangedFields = useCallback((labels: string[]) => {
    if (labels.length === 0) {
      return "פרטים";
    }

    return labels.join(", ");
  }, []);

  // Load all data from DB on mount
  useEffect(() => {
    const loadData = async () => {
      setIsLoading(true);
      try {
        // Identity is the Shan-AI session (PLAN-lessons-module.md decision 1).
        const me = await fetchMe();
        if (!me) {
          window.location.href = SHAN_LOGIN_URL;
          return;
        }
        const [profilesRes, projectsRes, lessonsRes, equipmentRes, notificationsRes, implRes, catRes, feedbackRes, refReviewsRes] = await Promise.all([
          supabase.from("profiles").select("*"),
          supabase.from("projects").select("*"),
          supabase.from("lessons").select("*").order("id", { ascending: false }),
          supabase.from("equipment").select("*"),
          supabase.from("notifications").select("*").order("id", { ascending: false }),
          supabase.from("lesson_implementations").select("*"),
          supabase.from("lesson_categories").select("*").order("id", { ascending: true }),
          supabase.from("feedback").select("*").order("created_at", { ascending: false }),
          supabase.from("referent_reviews" as any).select("*"),
        ]);

        const mapped = (profilesRes.data || []).map(mapDbUser);
        if (!mapped.some((u) => u.id === me.self.id)) mapped.push(me.self);
        setUsers(mapped);
        setIdentities([me.self, ...me.groups]);
        const remembered = sessionStorage.getItem(ACT_AS_KEY);
        setCurrentUser([me.self, ...me.groups].find((u) => u.id === remembered) ?? me.self);
        setIsLoggedIn(true);
        if (projectsRes.data) setProjects(projectsRes.data.map(mapDbProject));
        if (lessonsRes.data) setLessons(lessonsRes.data.map(mapDbLesson));
        if (equipmentRes.data) setEquipment(equipmentRes.data);
        if (notificationsRes.data) setNotifications(notificationsRes.data.map(mapDbNotification));
        if (implRes.data) setImplementations(implRes.data.map(mapDbImpl));
        if (catRes.data) setLessonCategories(catRes.data.map((c: any) => c.name));
        if (feedbackRes.data) setFeedbacks(feedbackRes.data.map(mapDbFeedback));
        if (refReviewsRes.data) setReferentReviews((refReviewsRes.data as any[]).map(mapDbReferentReview));
      } catch (e) {
        console.error("Error loading data:", e);
      } finally {
        setIsLoading(false);
      }
    };
    loadData();
  }, []);

  const hasPermission = (permission: string) =>
    permissions[currentUser.role]?.includes(permission) ?? false;

  // Email queue to avoid Resend rate limit (2 req/sec)
  const emailQueueRef = useRef<Promise<void>>(Promise.resolve());

  const sendNotificationEmail = useCallback((userEmail: string, subject: string, body: string) => {
    emailQueueRef.current = emailQueueRef.current.then(async () => {
      try {
        await supabase.functions.invoke("send-notification-email", {
          body: { to: userEmail, subject, body },
        });
        await new Promise((r) => setTimeout(r, 600));
      } catch (e) {
        console.error("Failed to send notification email:", e);
      }
    });
  }, []);

  const markNotificationsAsRead = useCallback(async () => {
    const unreadIds = notifications.filter((n) => n.userId === currentUser.id && !n.read).map((n) => n.id);
    if (unreadIds.length === 0) return;
    await supabase.from("notifications").update({ read: true }).in("id", unreadIds);
    setNotifications((prev) => prev.map((n) => unreadIds.includes(n.id) ? { ...n, read: true } : n));
  }, [notifications, currentUser.id]);

  const markNotificationsAsReadByIds = useCallback(async (ids: number[]) => {
    if (ids.length === 0) return;
    await supabase.from("notifications").update({ read: true }).in("id", ids);
    setNotifications((prev) => prev.map((n) => ids.includes(n.id) ? { ...n, read: true } : n));
  }, []);

  const markSingleNotificationAsRead = useCallback(async (id: number) => {
    await supabase.from("notifications").update({ read: true }).eq("id", id);
    setNotifications((prev) => prev.map((n) => n.id === id ? { ...n, read: true } : n));
  }, []);

  const clearNotifications = useCallback(async () => {
    const userNotifIds = notifications.filter((n) => n.userId === currentUser.id).map((n) => n.id);
    if (userNotifIds.length === 0) return;
    await supabase.from("notifications").delete().in("id", userNotifIds);
    setNotifications((prev) => prev.filter((n) => n.userId !== currentUser.id));
  }, [notifications, currentUser.id]);

  const addNotification = useCallback(async (
    userId: string,
    type: Notification["type"],
    title: string,
    description: string,
    entityId?: string | number,
    entityType?: Notification["entityType"],
  ) => {
    const now = new Date().toISOString();
    const insertData: any = { user_id: userId, type, title, description, time: now, read: false };
    const inferredEntityType: Notification["entityType"] | undefined = entityType ?? (
      type === "stage_change" ? "project" : title.includes("פידבק") ? "feedback" : "lesson"
    );
    if (inferredEntityType) insertData.entity_type = inferredEntityType;
    if (entityId !== undefined && entityId !== null) insertData.entity_id = String(entityId);
    const { data } = await supabase
      .from("notifications")
      .insert(insertData)
      .select()
      .single();
    if (data) {
      setNotifications((prev) => [mapDbNotification(data), ...prev]);
    }

    // Send email notification (if user opted in)
    const targetUser = users.find((u) => u.id === userId);
    if (targetUser?.email) {
      const prefs = targetUser.emailPreferences || defaultEmailPreferences;
      const prefKey = type as keyof EmailPreferences;
      if (prefs[prefKey] === false) return; // User opted out

      const emailSubjectMap: Record<string, string> = {
        new_lesson: "📋 לקח חדש ממתין לאישור",
        approved: "✅ הלקח שלך אושר",
        rejected: "❌ הלקח שלך נדחה",
        distributed: "📤 לקח הופץ לפרויקט שלך",
        stage_change: "🔄 שינוי בפרויקט",
        ai_suggestion: "🤖 לקחים רלוונטיים זוהו",
      };
      sendNotificationEmail(
        targetUser.email,
        emailSubjectMap[type] || title,
        `<p><strong>${title}</strong></p><p>${description}</p>`
      );
    }
  }, [users, sendNotificationEmail]);

  const notifySuggestedLessons = useCallback((projectId: number, projectName: string, stageName: string, suggestionCount: number) => {
    if (suggestionCount === 0) return;

    const notifiedUserIds = new Set<string>();
    users
      .filter((user) => (user.role === "project_manager" && user.assignedProjects.includes(projectId)) || user.role === "admin")
      .forEach((user) => {
        if (notifiedUserIds.has(user.id)) return;
        notifiedUserIds.add(user.id);
        addNotification(
          user.id,
          "ai_suggestion",
          `${suggestionCount} לקחים רלוונטיים זוהו`,
          `AI זיהה לקחים רלוונטיים ל${projectName} בשלב ${stageName} — נדרשת תגובתך`,
          projectId,
          "project"
        );
      });
  }, [users, addNotification]);

  const fetchRelevantLessonSuggestions = useCallback(async (
    projectId: number,
    stageIndexOverride?: number,
  ): Promise<{ projectAnalysis: ProjectAnalysis | null; suggestions: RelevantLessonSuggestion[] }> => {
    const project = projects.find((p) => p.id === projectId);
    if (!project) {
      return { projectAnalysis: null, suggestions: [] };
    }

    setIsAnalyzing(true);
    try {
      const currentStage = PROJECT_STAGES[stageIndexOverride ?? project.stageIndex];
      const feedbackHistory = implementations
        .filter((impl) => impl.respondedBy)
        .map((impl) => {
          const lesson = lessons.find((l) => l.id === impl.lessonId);
          return { lessonTitle: lesson?.title || "", isRelevant: impl.isRelevant, isImplemented: impl.isImplemented, notes: impl.notes };
        });

      const projectEquipment = equipment.filter((e) => project.equipmentIds.includes(e.id)).map((e) => e.name);
      const projectLessonsList = lessons.filter((l) => l.projectId === projectId).map((l) => ({
        id: l.id, title: l.title, category: l.category, stage: l.stage, risk: l.risk, status: l.status,
        description: l.description, recommendation: l.recommendation,
      }));
      const otherLessons = lessons.filter((l) => l.projectId !== projectId).map((l) => ({
        id: l.id, title: l.title, category: l.category, stage: l.stage, risk: l.risk, status: l.status,
        projectName: l.project,
        equipment: equipment.filter((e) => l.equipmentIds.includes(e.id)).map((e) => e.name),
      }));

      const referentsList = users.filter((u) => u.role === "referent" && u.assignedStageIndexes.length > 0).map((u) => ({
        id: u.id,
        name: u.name,
        stages: u.assignedStageIndexes.map((si) => PROJECT_STAGES[si]).filter(Boolean),
      }));

      const { data, error } = await supabase.functions.invoke("analyze-lessons", {
        body: {
          projectName: project.name,
          projectStage: currentStage,
          projectEquipment,
          projectLessons: projectLessonsList,
          allLessons: otherLessons,
          feedbackHistory,
          projectType: project.projectType,
          stationType: project.stationType,
          risk: project.risk,
          referents: referentsList,
          userId: currentUser.id,
        },
      });
      if (error) throw error;

      const projectAnalysis: ProjectAnalysis = data?.project_analysis || { summary: "", strengths: [], weaknesses: [], recommendations: [] };
      const relevantLessons = data?.relevant_lessons || [];
      const existingLessonIds = new Set(
        implementations
          .filter((impl) => impl.projectId === projectId)
          .map((impl) => impl.lessonId)
      );
      const suggestions = relevantLessons
        .map((lesson: any) => ({
          lessonId: lesson.lesson_id,
          reason: lesson.reason,
          priority: lesson.priority,
        } satisfies RelevantLessonSuggestion))
        .filter((lesson: RelevantLessonSuggestion) => !existingLessonIds.has(lesson.lessonId));

      return { projectAnalysis, suggestions };
    } catch (e) {
      console.error("AI analysis error:", e);
      return { projectAnalysis: null, suggestions: [] };
    } finally {
      setIsAnalyzing(false);
    }
  }, [projects, implementations, lessons, equipment, users]);

  const addSuggestedLessonsToProject = useCallback(async (projectId: number, suggestions: RelevantLessonSuggestion[]): Promise<number> => {
    if (suggestions.length === 0) return 0;

    const project = projects.find((p) => p.id === projectId);
    if (!project) return 0;

    const existingLessonIds = new Set(
      implementations
        .filter((impl) => impl.projectId === projectId)
        .map((impl) => impl.lessonId)
    );
    const newSuggestions = suggestions.filter((suggestion) => !existingLessonIds.has(suggestion.lessonId));
    if (newSuggestions.length === 0) return 0;

    const inserts = newSuggestions.map((suggestion) => ({
      lesson_id: suggestion.lessonId,
      project_id: projectId,
      reason: suggestion.reason,
      priority: suggestion.priority,
    }));

    const { data: implData, error } = await supabase.from("lesson_implementations").insert(inserts).select();
    if (error) throw error;

    if (implData) {
      setImplementations((prev) => [...prev, ...implData.map(mapDbImpl)]);
    }

    notifySuggestedLessons(projectId, project.name, PROJECT_STAGES[project.stageIndex], newSuggestions.length);
    return newSuggestions.length;
  }, [projects, implementations, notifySuggestedLessons]);

  const analyzeRelevantLessons = useCallback(async (projectId: number): Promise<ProjectAnalysis | null> => {
    const { projectAnalysis, suggestions } = await fetchRelevantLessonSuggestions(projectId);

    try {
      await addSuggestedLessonsToProject(projectId, suggestions);
    } catch (error) {
      console.error("AI suggestion insert error:", error);
    }

    return projectAnalysis;
  }, [fetchRelevantLessonSuggestions, addSuggestedLessonsToProject]);

  const respondToImplementation = useCallback(async (implId: number, response: { isRelevant: boolean; isImplemented: boolean; notes: string }) => {
    const respondedAt = new Date().toISOString();
    await supabase.from("lesson_implementations").update({
      is_relevant: response.isRelevant,
      is_implemented: response.isImplemented,
      notes: response.notes,
      responded_by: currentUser.id,
      responded_at: respondedAt,
    }).eq("id", implId);

    setImplementations((prev) =>
      prev.map((impl) => impl.id === implId ? { ...impl, isRelevant: response.isRelevant, isImplemented: response.isImplemented, notes: response.notes, respondedBy: currentUser.id, respondedAt } : impl)
    );

    const impl = implementations.find((i) => i.id === implId);
    if (impl) {
      const lesson = lessons.find((l) => l.id === impl.lessonId);
      const project = projects.find((p) => p.id === impl.projectId);
      users.filter((u) => u.role === "admin" && u.id !== currentUser.id).forEach((admin) => {
        addNotification(admin.id, "ai_suggestion", "פידבק התקבל על לקח", `${currentUser.name} הגיב על "${lesson?.title || ""}" ב${project?.name || ""}`, impl.lessonId, "lesson");
      });
    }
  }, [currentUser.id, implementations, lessons, projects, users, addNotification]);

  const respondToReferentReview = useCallback(async (reviewId: number, response: { isRelevant: boolean; isImplemented: boolean; notes: string }) => {
    const respondedAt = new Date().toISOString();
    await supabase.from("referent_reviews" as any).update({
      is_relevant: response.isRelevant,
      is_implemented: response.isImplemented,
      notes: response.notes,
      responded_at: respondedAt,
    }).eq("id", reviewId);

    setReferentReviews((prev) =>
      prev.map((r) => r.id === reviewId ? { ...r, isRelevant: response.isRelevant, isImplemented: response.isImplemented, notes: response.notes, respondedAt } : r)
    );

    const review = referentReviews.find((r) => r.id === reviewId);
    if (review) {
      const lesson = lessons.find((l) => l.id === review.lessonId);
      void recordWorkflowEvent({
        lessonId: review.lessonId,
        eventType: WORKFLOW_EVENT_TYPES.REFERENT_RESPONSE,
        statusAfter: lesson?.workflowStatus ?? null,
        note: [
          response.isRelevant ? "רלוונטי" : "לא רלוונטי",
          response.isImplemented ? "יושם" : "טרם יושם",
          response.notes,
        ].filter(Boolean).join(" · "),
      });
      users.filter((u) => u.role === "admin" && u.id !== currentUser.id).forEach((admin) => {
        addNotification(admin.id, "ai_suggestion", "פידבק רפרנט התקבל", `${currentUser.name} (רפרנט) הגיב על "${lesson?.title || ""}"`, review.lessonId, "lesson");
      });
    }
  }, [currentUser.id, currentUser.name, referentReviews, lessons, users, addNotification, recordWorkflowEvent]);


  const updateProjectStage = useCallback(async (projectId: number, newStageIndex: number) => {
    // Backward-compat single-stage update: replace stage_indexes with [newStageIndex]
    const newStageIndexes = [newStageIndex];
    await supabase.from("projects").update({ stage_index: newStageIndex, stage_indexes: newStageIndexes }).eq("id", projectId);
    setProjects((prev) => prev.map((p) => (p.id === projectId ? { ...p, stageIndex: newStageIndex, stageIndexes: newStageIndexes } : p)));

    const project = projects.find((p) => p.id === projectId);
    if (project) {
      const newStageName = PROJECT_STAGES[newStageIndex];
      users.filter((u) => u.assignedProjects.includes(projectId) && u.id !== currentUser.id).forEach((pm) => {
        addNotification(pm.id, "stage_change", "שינוי שלב בפרויקט", `${project.name} עבר לשלב: ${newStageName}`, projectId, "project");
      });
    }
  }, [projects, currentUser.id, addNotification, users]);

  const updateProjectStages = useCallback(async (projectId: number, newStageIndexes: number[]) => {
    if (!newStageIndexes || newStageIndexes.length === 0) {
      throw new Error("חובה לבחור לפחות שלב אחד");
    }
    const sorted = [...new Set(newStageIndexes)].sort((a, b) => a - b);
    const derivedStageIndex = Math.max(...sorted);
    const project = projects.find((p) => p.id === projectId);
    const previousStages = new Set(project?.stageIndexes || []);
    const addedStages = sorted.filter((s) => !previousStages.has(s));

    await supabase.from("projects").update({ stage_index: derivedStageIndex, stage_indexes: sorted }).eq("id", projectId);
    setProjects((prev) => prev.map((p) => (p.id === projectId ? { ...p, stageIndex: derivedStageIndex, stageIndexes: sorted } : p)));

    if (project && addedStages.length > 0) {
      const addedNames = addedStages.map((i) => PROJECT_STAGES[i]).filter(Boolean).join(", ");
      users.filter((u) => u.assignedProjects.includes(projectId) && u.id !== currentUser.id).forEach((pm) => {
        addNotification(pm.id, "stage_change", "שינוי שלב בפרויקט", `${project.name} — שלבים פעילים חדשים: ${addedNames}`, projectId, "project");
      });
    }
  }, [projects, currentUser.id, addNotification, users]);

  const addLesson = useCallback(async (lesson: Omit<Lesson, "id" | "date" | "status" | "createdBy" | "workflowStatus">) => {
    const date = new Date().toISOString().split("T")[0];
    const targetDateObj = new Date();
    targetDateObj.setDate(targetDateObj.getDate() + 10);
    const targetDate = targetDateObj.toISOString().split("T")[0];

    const insertData: any = {
      title: lesson.title,
      description: lesson.description,
      recommendation: lesson.recommendation,
      project_name: lesson.project,
      project_id: lesson.projectId ?? null,
      stage: lesson.stage,
      category: lesson.category,
      risk: lesson.risk,
      status: "new",
      date,
      created_by: currentUser.id,
      equipment_ids: lesson.equipmentIds,
      workflow_status: "חדש",
      target_date: targetDate,
    };
    if (lesson.professionalDomain) insertData.professional_domain = lesson.professionalDomain;
    if (lesson.eventDate) insertData.event_date = lesson.eventDate;
    if (lesson.assignedTo) insertData.assigned_to = lesson.assignedTo;
    if (lesson.impactScheduleDelay !== undefined) insertData.impact_schedule_delay = lesson.impactScheduleDelay;
    if (lesson.impactBudgetCost !== undefined) insertData.impact_budget_cost = lesson.impactBudgetCost;
    if (lesson.impactQualityDesc) insertData.impact_quality_desc = lesson.impactQualityDesc;
    if (lesson.fileUrl) insertData.file_url = lesson.fileUrl;

    const { data } = await supabase.from("lessons").insert(insertData).select().single();

    if (data) {
      const createdLesson = mapDbLesson(data);
      setLessons((prev) => [createdLesson, ...prev]);
      void recordWorkflowEvent({
        lessonId: createdLesson.id,
        eventType: WORKFLOW_EVENT_TYPES.CREATED,
        statusAfter: createdLesson.workflowStatus,
      });
      void logActivity({
        actionType: "create",
        entityType: "lesson",
        entityId: createdLesson.id,
        objectLabel: `לקח #${createdLesson.id}`,
        description: `יצר לקח: ${createdLesson.title}`,
      });
    }

    const creator = users.find((u) => u.id === currentUser.id);
    if (data) addNotification("u1", "new_lesson", "לקח חדש ממתין לאישור", `${lesson.title} (מאת ${creator?.name})`, data.id, "lesson");
  }, [currentUser.id, addNotification, users, logActivity, recordWorkflowEvent]);

  const approveLesson = useCallback(async (lessonId: number) => {
    await supabase.from("lessons").update({ status: "approved", approved_by: currentUser.id }).eq("id", lessonId);
    setLessons((prev) => prev.map((l) => l.id === lessonId ? { ...l, status: "approved" as LessonStatus, approvedBy: currentUser.id } : l));

    const lesson = lessons.find((l) => l.id === lessonId);
    if (lesson) {
      void recordWorkflowEvent({
        lessonId,
        eventType: WORKFLOW_EVENT_TYPES.APPROVED,
        statusAfter: lesson.workflowStatus,
      });
      addNotification(lesson.createdBy, "approved", "הלקח שלך אושר!", lesson.title, lessonId, "lesson");
    }
  }, [currentUser.id, lessons, addNotification, recordWorkflowEvent]);

  const rejectLesson = useCallback(async (lessonId: number) => {
    await supabase.from("lessons").update({ status: "rejected" }).eq("id", lessonId);
    setLessons((prev) => prev.map((l) => l.id === lessonId ? { ...l, status: "rejected" as LessonStatus } : l));

    const lesson = lessons.find((l) => l.id === lessonId);
    if (lesson) {
      void recordWorkflowEvent({
        lessonId,
        eventType: WORKFLOW_EVENT_TYPES.REJECTED,
        statusAfter: lesson.workflowStatus,
      });
      addNotification(lesson.createdBy, "rejected", "הלקח שלך נדחה", lesson.title, lessonId, "lesson");
    }
  }, [lessons, addNotification, recordWorkflowEvent]);

  const closeLesson = useCallback(async (lessonId: number) => {
    await supabase.from("lessons").update({ status: "closed" }).eq("id", lessonId);
    setLessons((prev) => prev.map((l) => l.id === lessonId ? { ...l, status: "closed" as LessonStatus } : l));
    const lesson = lessons.find((l) => l.id === lessonId);
    if (lesson) {
      void recordWorkflowEvent({
        lessonId,
        eventType: WORKFLOW_EVENT_TYPES.CLOSED,
        statusAfter: lesson.workflowStatus,
      });
      addNotification(lesson.createdBy, "approved", "לקח נסגר", lesson.title, lessonId, "lesson");
    }
  }, [lessons, addNotification, recordWorkflowEvent]);


  const removeLessonFromProject = useCallback(async (lessonId: number, projectId: number) => {
    const lesson = lessons.find((l) => l.id === lessonId);
    if (!lesson) return;
    const newDistributedTo = (lesson.distributedTo || []).filter((pid) => pid !== projectId);
    await supabase.from("lessons").update({ distributed_to: newDistributedTo }).eq("id", lessonId);
    // Also remove implementation record
    await supabase.from("lesson_implementations").delete().eq("lesson_id", lessonId).eq("project_id", projectId);
    setLessons((prev) => prev.map((l) => l.id === lessonId ? { ...l, distributedTo: newDistributedTo } : l));
  }, [lessons]);

  const deleteLesson = useCallback(async (lessonId: number) => {
    const lesson = lessons.find((item) => item.id === lessonId);
    await supabase.from("lessons").delete().eq("id", lessonId);
    setLessons((prev) => prev.filter((l) => l.id !== lessonId));

    if (lesson) {
      void logActivity({
        actionType: "delete",
        entityType: "lesson",
        entityId: lesson.id,
        objectLabel: `לקח #${lesson.id}`,
        description: `מחק לקח: ${lesson.title}`,
      });
    }
  }, [lessons, logActivity]);

  const distributeLesson = useCallback(async (
    lessonId: number,
    targetProjectIds?: number[],
    targetReferentIds?: string[],
    distributionSource: "manual" | "ai" = "manual",
  ) => {
    const lesson = lessons.find((l) => l.id === lessonId);
    if (!lesson) return;

    const relevantProjectIds = targetProjectIds || projects.filter((p) => p.id !== lesson.projectId).map((p) => p.id);

    // Find relevant referents based on stage overlap
    const lessonStageIndex = PROJECT_STAGES.indexOf(lesson.stage);
    const hasExplicitReferentTargets = Array.isArray(targetReferentIds);
    let referentIds: string[];
    let relevantReferents: MockUser[];

    if (hasExplicitReferentTargets) {
      relevantReferents = users.filter((u) => targetReferentIds.includes(u.id));
      referentIds = relevantReferents.map((r) => r.id);
    } else {
      relevantReferents = users.filter(
        (u) => u.role === "referent" && u.assignedStageIndexes.includes(lessonStageIndex)
      );
      referentIds = relevantReferents.map((r) => r.id);
    }

    const referentDistributionReason = distributionSource === "ai"
      ? "הופץ אוטומטית לפי המלצת AI והתאמת שלב"
      : hasExplicitReferentTargets
        ? "הופץ ידנית"
        : "הופץ אוטומטית לפי שלב משויך";

    // When distributing to referents, also set workflow to "בטיפול רפרנט"
    const referentStartDate = new Date().toISOString().split("T")[0];
    const firstReferentId = referentIds.length > 0 ? referentIds[0] : null;

    const lessonUpdate: any = {
      status: "distributed",
      distributed_to: relevantProjectIds,
      distributed_to_referents: referentIds.length > 0 ? referentIds : null,
    };

    // If referents are assigned, activate the workflow
    if (firstReferentId) {
      lessonUpdate.assigned_to = firstReferentId;
      lessonUpdate.workflow_status = "בטיפול רפרנט";
      lessonUpdate.referent_start_date = referentStartDate;
    }

    await supabase.from("lessons").update(lessonUpdate).eq("id", lessonId);

    setLessons((prev) => prev.map((l) => l.id === lessonId ? {
      ...l,
      status: "distributed" as LessonStatus,
      distributedTo: relevantProjectIds,
      distributedToReferents: referentIds.length > 0 ? referentIds : undefined,
      ...(firstReferentId ? {
        assignedTo: firstReferentId,
        workflowStatus: "בטיפול רפרנט",
        referentStartDate,
      } : {}),
    } : l));

    relevantProjectIds.forEach((pid) => {
      const project = projects.find((p) => p.id === pid);
      if (project) {
        addNotification(project.managerId, "distributed", "לקח הופץ לפרויקט שלך", `${lesson.title} — הופץ ל${project.name}`, lessonId, "lesson");
      }
    });

    // Create referent review records and notify referents
    if (referentIds.length > 0) {
      const refInserts = referentIds.map((rid) => ({
        lesson_id: lessonId,
        referent_id: rid,
        reason: referentDistributionReason,
        priority: "medium",
      }));
      const { data: refData } = await supabase.from("referent_reviews" as any).insert(refInserts).select();
      if (refData) {
        setReferentReviews((prev) => [...prev, ...(refData as any[]).map(mapDbReferentReview)]);
      }
      relevantReferents.forEach((ref) => {
        addNotification(ref.id, "distributed", "לקח הופץ אליך", `${lesson.title} — רלוונטי לשלבים שלך`, lessonId, "lesson");
      });
    }

    // History: distribution, and referent assignment when the workflow moved forward
    const distributionTargets = [
      relevantProjectIds.length > 0 ? `${relevantProjectIds.length} פרויקטים` : "",
      relevantReferents.length > 0 ? `רפרנטים: ${relevantReferents.map((r) => r.name).join(", ")}` : "",
    ].filter(Boolean).join(" · ");

    void recordWorkflowEvent({
      lessonId,
      eventType: WORKFLOW_EVENT_TYPES.DISTRIBUTED,
      statusAfter: firstReferentId ? "בטיפול רפרנט" : lesson.workflowStatus,
      note: `${referentDistributionReason}${distributionTargets ? ` — ${distributionTargets}` : ""}`,
    });

    if (firstReferentId) {
      void recordWorkflowEvent({
        lessonId,
        eventType: WORKFLOW_EVENT_TYPES.ASSIGNED_REFERENT,
        statusAfter: "בטיפול רפרנט",
        targetUserId: firstReferentId,
        targetUserName: users.find((u) => u.id === firstReferentId)?.name ?? null,
      });
    }

    relevantProjectIds.forEach((pid) => {
      setTimeout(() => analyzeRelevantLessons(pid), 100);
    });
  }, [lessons, projects, users, addNotification, analyzeRelevantLessons, recordWorkflowEvent]);


  const unreadCount = notifications.filter((n) => n.userId === currentUser.id && !n.read).length;

  const updateProjectEquipment = useCallback(async (projectId: number, equipmentIds: number[]) => {
    await supabase.from("projects").update({ equipment_ids: equipmentIds }).eq("id", projectId);
    setProjects((prev) => prev.map((p) => (p.id === projectId ? { ...p, equipmentIds } : p)));

    const project = projects.find((p) => p.id === projectId);
    if (project) {
      users.filter((u) => (u.assignedProjects.includes(projectId) || u.role === "admin") && u.id !== currentUser.id).forEach((u) => {
        addNotification(u.id, "stage_change", "שינוי ציוד בפרויקט", `הציוד עודכן ב${project.name}`, projectId, "project");
      });
    }
    setTimeout(() => analyzeRelevantLessons(projectId), 100);
  }, [analyzeRelevantLessons, projects, users, currentUser.id, addNotification]);

  const addEquipment = useCallback(async (eq: Omit<Equipment, "id">, projectIds?: number[]): Promise<Equipment | undefined> => {
    const { data } = await supabase.from("equipment").insert(eq).select().single();
    if (data) {
      setEquipment((prev) => [...prev, data]);
      if (projectIds && projectIds.length > 0) {
        for (const pid of projectIds) {
          const project = projects.find((p) => p.id === pid);
          if (project && !project.equipmentIds.includes(data.id)) {
            const newIds = [...project.equipmentIds, data.id];
            await supabase.from("projects").update({ equipment_ids: newIds }).eq("id", pid);
          }
        }
        setProjects((prev) => prev.map((p) =>
          projectIds.includes(p.id) && !p.equipmentIds.includes(data.id)
            ? { ...p, equipmentIds: [...p.equipmentIds, data.id] }
            : p
        ));
      }

      void logActivity({
        actionType: "create",
        entityType: "equipment",
        entityId: data.id,
        objectLabel: data.name,
        description: `יצר פריט ציוד: ${data.name}`,
      });
      return data;
    }
    return undefined;
  }, [projects, logActivity]);

  const updateEquipment = useCallback(async (eqId: number, updates: Partial<Pick<Equipment, "name" | "category">>) => {
    const existingEquipment = equipment.find((item) => item.id === eqId);
    await supabase.from("equipment").update(updates).eq("id", eqId);
    setEquipment((prev) => prev.map((e) => e.id === eqId ? { ...e, ...updates } : e));

    if (existingEquipment) {
      const changedFields = describeChangedFields([
        updates.name !== undefined ? "שם" : "",
        updates.category !== undefined ? "קטגוריה" : "",
      ].filter(Boolean));

      void logActivity({
        actionType: "update",
        entityType: "equipment",
        entityId: eqId,
        objectLabel: updates.name || existingEquipment.name,
        description: `עדכן פריט ציוד ${existingEquipment.name} (${changedFields})`,
      });
    }
  }, [equipment, describeChangedFields, logActivity]);

  const renameCategory = useCallback(async (oldName: string, newName: string) => {
    const eqInCategory = equipment.filter((e) => e.category === oldName);
    for (const eq of eqInCategory) {
      await supabase.from("equipment").update({ category: newName }).eq("id", eq.id);
    }
    setEquipment((prev) => prev.map((e) => e.category === oldName ? { ...e, category: newName } : e));
    void logActivity({
      actionType: "update",
      entityType: "category",
      entityId: oldName,
      objectLabel: newName,
      description: `שינה קטגוריית ציוד מ-${oldName} ל-${newName}`,
    });
  }, [equipment, logActivity]);

  const deleteCategory = useCallback(async (categoryName: string) => {
    const eqInCategory = equipment.filter((e) => e.category === categoryName);
    for (const eq of eqInCategory) {
      await supabase.from("equipment").delete().eq("id", eq.id);
    }
    setEquipment((prev) => prev.filter((e) => e.category !== categoryName));
    const eqIds = eqInCategory.map((e) => e.id);
    const affectedProjects = projects.filter((p) => p.equipmentIds.some((id) => eqIds.includes(id)));
    for (const p of affectedProjects) {
      const newIds = p.equipmentIds.filter((id) => !eqIds.includes(id));
      await supabase.from("projects").update({ equipment_ids: newIds }).eq("id", p.id);
    }
    setProjects((prev) => prev.map((p) => ({ ...p, equipmentIds: p.equipmentIds.filter((id) => !eqIds.includes(id)) })));
    void logActivity({
      actionType: "delete",
      entityType: "category",
      entityId: categoryName,
      objectLabel: categoryName,
      description: `מחק קטגוריית ציוד: ${categoryName}`,
    });
  }, [equipment, projects, logActivity]);

  const removeEquipment = useCallback(async (eqId: number) => {
    const equipmentItem = equipment.find((item) => item.id === eqId);
    await supabase.from("equipment").delete().eq("id", eqId);
    setEquipment((prev) => prev.filter((e) => e.id !== eqId));
    const affectedProjects = projects.filter((p) => p.equipmentIds.includes(eqId));
    for (const p of affectedProjects) {
      const newIds = p.equipmentIds.filter((id) => id !== eqId);
      await supabase.from("projects").update({ equipment_ids: newIds }).eq("id", p.id);
    }
    setProjects((prev) => prev.map((p) => ({ ...p, equipmentIds: p.equipmentIds.filter((id) => id !== eqId) })));

    if (equipmentItem) {
      void logActivity({
        actionType: "delete",
        entityType: "equipment",
        entityId: equipmentItem.id,
        objectLabel: equipmentItem.name,
        description: `מחק פריט ציוד: ${equipmentItem.name}`,
      });
    }
  }, [equipment, projects, logActivity]);

  const updateUser = useCallback(async (userId: string, updates: Partial<Pick<MockUser, "name" | "email" | "role" | "assignedProjects" | "assignedEquipmentIds" | "assignedStageIndexes">>) => {
    const dbUpdates: any = {};
    if (updates.name !== undefined) dbUpdates.name = updates.name;
    if (updates.email !== undefined) dbUpdates.email = updates.email;
    if (updates.role !== undefined) dbUpdates.role = updates.role;
    if (updates.assignedProjects !== undefined) dbUpdates.assigned_projects = updates.assignedProjects;
    if (updates.assignedEquipmentIds !== undefined) dbUpdates.assigned_equipment_ids = updates.assignedEquipmentIds;
    if (updates.assignedStageIndexes !== undefined) dbUpdates.assigned_stage_indexes = updates.assignedStageIndexes;

    const targetUser = users.find((u) => u.id === userId);
    await supabase.from("profiles").update(dbUpdates).eq("id", userId);
    setUsers((prev) => prev.map((u) => (u.id === userId ? { ...u, ...updates } : u)));
    if (currentUser.id === userId) {
      setCurrentUser((prev) => ({ ...prev, ...updates }));
    }

    // Sync manager_id in projects when assignedProjects changes for a project_manager
    if (updates.assignedProjects !== undefined && targetUser) {
      const userRole = updates.role || targetUser.role;
      if (userRole === "project_manager") {
        const oldAssigned = targetUser.assignedProjects || [];
        const newAssigned = updates.assignedProjects;

        // Projects added to this manager
        const addedProjects = newAssigned.filter((pid) => !oldAssigned.includes(pid));
        for (const pid of addedProjects) {
          // Update manager_id in projects table
          await supabase.from("projects").update({ manager_id: userId }).eq("id", pid);
          setProjects((prev) => prev.map((p) => p.id === pid ? { ...p, managerId: userId } : p));

          // Remove this project from other managers' assigned_projects
          const otherManagers = users.filter((u) => u.role === "project_manager" && u.id !== userId && u.assignedProjects.includes(pid));
          for (const otherMgr of otherManagers) {
            const cleaned = otherMgr.assignedProjects.filter((id) => id !== pid);
            await supabase.from("profiles").update({ assigned_projects: cleaned }).eq("id", otherMgr.id);
            setUsers((prev) => prev.map((u) => u.id === otherMgr.id ? { ...u, assignedProjects: cleaned } : u));
          }
        }
      }
    }
    if (userId !== currentUser.id) {
      addNotification(userId, "stage_change", "פרטי המשתמש שלך עודכנו", `הפרופיל של ${targetUser?.name || ""} עודכן על ידי ${currentUser.name}`);
    }

    if (targetUser) {
      {
        const changedFields = describeChangedFields([
          updates.name !== undefined ? "שם" : "",
          updates.email !== undefined ? "אימייל" : "",
          updates.role !== undefined ? "תפקיד" : "",
          updates.assignedProjects !== undefined ? "פרויקטים" : "",
          updates.assignedEquipmentIds !== undefined ? "ציוד" : "",
          updates.assignedStageIndexes !== undefined ? "שלבים" : "",
        ].filter(Boolean));

        void logActivity({
          actionType: "update",
          entityType: "user",
          entityId: targetUser.id,
          objectLabel: updates.name || targetUser.name,
          description: `עדכן משתמש ${targetUser.name} (${changedFields})`,
        });
      }
    }
  }, [currentUser.id, currentUser.name, users, addNotification, describeChangedFields, logActivity]);

  const addUser = useCallback(async (user: Omit<MockUser, "id">) => {
    const id = `u${Date.now()}`;
    const { data } = await supabase.from("profiles" as any).insert({
      id,
      name: user.name,
      email: user.email,
      role: user.role,
      assigned_projects: user.assignedProjects,
      assigned_equipment_ids: user.assignedEquipmentIds || [],
      assigned_stage_indexes: user.assignedStageIndexes || [],
      email_preferences: user.emailPreferences,
    }).select().single();
    if (data) {
      const createdUser = mapDbUser(data);
      setUsers((prev) => [...prev, createdUser]);
      void logActivity({
        actionType: "create",
        entityType: "user",
        entityId: createdUser.id,
        objectLabel: createdUser.name,
        description: `יצר משתמש: ${createdUser.name}`,
      });
    }
  }, [logActivity]);

  const deleteUser = useCallback(async (userId: string) => {
    const targetUser = users.find((user) => user.id === userId);
    await supabase.from("profiles").delete().eq("id", userId);
    setUsers((prev) => prev.filter((u) => u.id !== userId));

    if (targetUser) {
      void logActivity({
        actionType: "delete",
        entityType: "user",
        entityId: targetUser.id,
        objectLabel: targetUser.name,
        description: `מחק משתמש: ${targetUser.name}`,
      });
    }
  }, [users, logActivity]);

  const updateEmailPreferences = useCallback(async (userId: string, prefs: EmailPreferences) => {
    await supabase.from("profiles").update({ email_preferences: prefs as any }).eq("id", userId);
    setUsers((prev) => prev.map((u) => (u.id === userId ? { ...u, emailPreferences: prefs } : u)));
    if (currentUser.id === userId) {
      setCurrentUser((prev) => ({ ...prev, emailPreferences: prefs }));
    }
  }, [currentUser.id]);

  const deleteProject = useCallback(async (projectId: number) => {
    const project = projects.find((item) => item.id === projectId);
    await supabase.from("lessons").delete().eq("project_id", projectId);
    await supabase.from("lesson_implementations").delete().eq("project_id", projectId);
    await supabase.from("projects").delete().eq("id", projectId);
    setProjects((prev) => prev.filter((p) => p.id !== projectId));
    setLessons((prev) => prev.filter((l) => l.projectId !== projectId));
    setImplementations((prev) => prev.filter((i) => i.projectId !== projectId));

    if (project) {
      void logActivity({
        actionType: "delete",
        entityType: "project",
        entityId: project.id,
        objectLabel: project.name,
        description: `מחק פרויקט: ${project.name}`,
      });
    }
  }, [projects, logActivity]);

  const updateProject = useCallback(async (projectId: number, updates: Partial<Pick<Project, "name" | "risk" | "projectType" | "stationType" | "managerId" | "site">>) => {
    const dbUpdates: any = {};
    if (updates.name !== undefined) dbUpdates.name = updates.name;
    if (updates.risk !== undefined) dbUpdates.risk = updates.risk;
    if (updates.projectType !== undefined) dbUpdates.project_type = updates.projectType;
    if (updates.stationType !== undefined) dbUpdates.station_type = updates.stationType;
    if (updates.managerId !== undefined) dbUpdates.manager_id = updates.managerId;
    if (updates.site !== undefined) dbUpdates.site = updates.site || null;

    const project = projects.find((p) => p.id === projectId);
    await supabase.from("projects").update(dbUpdates).eq("id", projectId);
    setProjects((prev) => prev.map((p) => (p.id === projectId ? { ...p, ...updates } : p)));

    if (updates.name !== undefined) {
      await supabase.from("lessons").update({ project_name: updates.name }).eq("project_id", projectId);
      setLessons((prev) => prev.map((l) => l.projectId === projectId ? { ...l, project: updates.name! } : l));
    }

    // Sync assigned_projects when manager changes
    if (updates.managerId !== undefined && project && updates.managerId !== project.managerId) {
      // Remove from old manager
      const oldManager = users.find((u) => u.id === project.managerId);
      if (oldManager) {
        const cleaned = oldManager.assignedProjects.filter((pid) => pid !== projectId);
        await supabase.from("profiles").update({ assigned_projects: cleaned }).eq("id", oldManager.id);
        setUsers((prev) => prev.map((u) => u.id === oldManager.id ? { ...u, assignedProjects: cleaned } : u));
        if (currentUser.id === oldManager.id) {
          setCurrentUser((prev) => ({ ...prev, assignedProjects: cleaned }));
        }
      }
      // Add to new manager
      const newManager = users.find((u) => u.id === updates.managerId);
      if (newManager) {
        const updatedAssigned = [...new Set([...newManager.assignedProjects, projectId])];
        await supabase.from("profiles").update({ assigned_projects: updatedAssigned }).eq("id", newManager.id);
        setUsers((prev) => prev.map((u) => u.id === newManager.id ? { ...u, assignedProjects: updatedAssigned } : u));
        if (currentUser.id === newManager.id) {
          setCurrentUser((prev) => ({ ...prev, assignedProjects: updatedAssigned }));
        }
      }
      // Remove from any other manager who has it
      const otherManagers = users.filter((u) => u.role === "project_manager" && u.id !== updates.managerId && u.id !== project.managerId && u.assignedProjects.includes(projectId));
      for (const otherMgr of otherManagers) {
        const cleaned = otherMgr.assignedProjects.filter((pid) => pid !== projectId);
        await supabase.from("profiles").update({ assigned_projects: cleaned }).eq("id", otherMgr.id);
        setUsers((prev) => prev.map((u) => u.id === otherMgr.id ? { ...u, assignedProjects: cleaned } : u));
      }
    }

    if (project) {
      const affectedUserIds = new Set<string>();
      affectedUserIds.add(project.managerId);
      if (updates.managerId) affectedUserIds.add(updates.managerId);
      users.filter((u) => u.role === "admin").forEach((u) => affectedUserIds.add(u.id));
      affectedUserIds.delete(currentUser.id);
      affectedUserIds.forEach((uid) => {
        addNotification(uid, "stage_change", "עדכון פרטי פרויקט", `פרטי ${project.name} עודכנו`, projectId, "project");
      });

      const changedFields = describeChangedFields([
        updates.name !== undefined ? "שם" : "",
        updates.risk !== undefined ? "רמת סיכון" : "",
        updates.projectType !== undefined ? "סוג פרויקט" : "",
        updates.stationType !== undefined ? "סוג תחנה" : "",
        updates.managerId !== undefined ? "מנהל פרויקט" : "",
        updates.site !== undefined ? "אתר" : "",
      ].filter(Boolean));

      void logActivity({
        actionType: "update",
        entityType: "project",
        entityId: project.id,
        objectLabel: updates.name || project.name,
        description: `עדכן פרויקט ${project.name} (${changedFields})`,
      });
    }
  }, [projects, users, currentUser.id, addNotification, describeChangedFields, logActivity]);

  const addProject = useCallback(async (project: Omit<Project, "id" | "lessonsCount">) => {
    const stageIndexes = project.stageIndexes && project.stageIndexes.length > 0
      ? project.stageIndexes
      : [project.stageIndex ?? 0];
    const derivedStageIndex = Math.max(...stageIndexes);
    const insertData: any = {
      name: project.name,
      stage_index: derivedStageIndex,
      stage_indexes: stageIndexes,
      risk: project.risk,
      lessons_count: 0,
      equipment_ids: project.equipmentIds,
      project_type: project.projectType,
      station_type: project.stationType,
      manager_id: project.managerId,
    };
    if (project.site) insertData.site = project.site;
    const { data } = await supabase.from("projects").insert(insertData).select().single();

    if (data) {
      const createdProject = mapDbProject(data);
      setProjects((prev) => [...prev, createdProject]);

      // Sync assigned_projects: add to selected manager, remove from others
      const manager = users.find((u) => u.id === project.managerId);
      if (manager) {
        const updatedAssigned = [...new Set([...manager.assignedProjects, createdProject.id])];
        await supabase.from("profiles").update({ assigned_projects: updatedAssigned }).eq("id", manager.id);
        setUsers((prev) => prev.map((u) => u.id === manager.id ? { ...u, assignedProjects: updatedAssigned } : u));
        if (currentUser.id === manager.id) {
          setCurrentUser((prev) => ({ ...prev, assignedProjects: updatedAssigned }));
        }
      }

      // Remove project from any other project_manager's assigned_projects
      const otherManagers = users.filter((u) => u.role === "project_manager" && u.id !== project.managerId && u.assignedProjects.includes(createdProject.id));
      for (const otherMgr of otherManagers) {
        const cleaned = otherMgr.assignedProjects.filter((pid) => pid !== createdProject.id);
        await supabase.from("profiles").update({ assigned_projects: cleaned }).eq("id", otherMgr.id);
        setUsers((prev) => prev.map((u) => u.id === otherMgr.id ? { ...u, assignedProjects: cleaned } : u));
      }

      void logActivity({
        actionType: "create",
        entityType: "project",
        entityId: createdProject.id,
        objectLabel: createdProject.name,
        description: `יצר פרויקט: ${createdProject.name}`,
      });
    }
    if (project.managerId !== currentUser.id) {
      addNotification(project.managerId, "stage_change", "פרויקט חדש הוקצה לך", `${project.name} — שלב: ${PROJECT_STAGES[derivedStageIndex]}`, data?.id || null, "project");
    }
  }, [currentUser.id, users, addNotification, logActivity]);

  const updateLessonAIReview = useCallback(async (lessonId: number, aiReview: LessonAIReview) => {
    await supabase.from("lessons").update({ ai_review: aiReview as any }).eq("id", lessonId);
    setLessons((prev) => prev.map((l) => (l.id === lessonId ? { ...l, aiReview } : l)));
  }, []);

  const updateLesson = useCallback(async (lessonId: number, updates: Partial<Pick<Lesson, "title" | "description" | "recommendation" | "stage" | "category" | "risk" | "equipmentIds" | "professionalDomain" | "eventDate" | "assignedTo" | "workflowStatus" | "impactScheduleDelay" | "impactBudgetCost" | "impactQualityDesc" | "fileUrl" | "referentStartDate" | "targetDate" | "closedDate">>) => {
    const dbUpdates: any = {};
    if (updates.title !== undefined) dbUpdates.title = updates.title;
    if (updates.description !== undefined) dbUpdates.description = updates.description;
    if (updates.recommendation !== undefined) dbUpdates.recommendation = updates.recommendation;
    if (updates.stage !== undefined) dbUpdates.stage = updates.stage;
    if (updates.category !== undefined) dbUpdates.category = updates.category;
    if (updates.risk !== undefined) dbUpdates.risk = updates.risk;
    if (updates.equipmentIds !== undefined) dbUpdates.equipment_ids = updates.equipmentIds;
    if (updates.professionalDomain !== undefined) dbUpdates.professional_domain = updates.professionalDomain;
    if (updates.eventDate !== undefined) dbUpdates.event_date = updates.eventDate;
    if (updates.assignedTo !== undefined) dbUpdates.assigned_to = updates.assignedTo;
    if (updates.workflowStatus !== undefined) dbUpdates.workflow_status = updates.workflowStatus;
    if (updates.impactScheduleDelay !== undefined) dbUpdates.impact_schedule_delay = updates.impactScheduleDelay;
    if (updates.impactBudgetCost !== undefined) dbUpdates.impact_budget_cost = updates.impactBudgetCost;
    if (updates.impactQualityDesc !== undefined) dbUpdates.impact_quality_desc = updates.impactQualityDesc;
    if (updates.fileUrl !== undefined) dbUpdates.file_url = updates.fileUrl;
    if (updates.referentStartDate !== undefined) dbUpdates.referent_start_date = updates.referentStartDate;
    if (updates.targetDate !== undefined) dbUpdates.target_date = updates.targetDate;
    if (updates.closedDate !== undefined) dbUpdates.closed_date = updates.closedDate;

    const lesson = lessons.find((item) => item.id === lessonId);
    const { error } = await supabase.from("lessons").update(dbUpdates).eq("id", lessonId);
    if (error) throw error;
    setLessons((prev) => prev.map((l) => (l.id === lessonId ? { ...l, ...updates } : l)));

    if (lesson) {
      const changedFields = describeChangedFields([
        updates.title !== undefined ? "כותרת" : "",
        updates.description !== undefined ? "תיאור" : "",
        updates.recommendation !== undefined ? "המלצה" : "",
        updates.stage !== undefined ? "שלב" : "",
        updates.category !== undefined ? "קטגוריה" : "",
        updates.risk !== undefined ? "רמת סיכון" : "",
        updates.assignedTo !== undefined ? "רפרנט" : "",
        updates.workflowStatus !== undefined ? "סטטוס טיפול" : "",
      ].filter(Boolean));

      void logActivity({
        actionType: "update",
        entityType: "lesson",
        entityId: lesson.id,
        objectLabel: `לקח #${lesson.id}`,
        description: `עדכן לקח ${lesson.title} (${changedFields})`,
      });
    }
  }, [lessons, describeChangedFields, logActivity]);

  const updateWorkflowStatus = useCallback(async (lessonId: number, status: string, note?: string, eventType?: string) => {
    const dbUpdates: any = { workflow_status: status };
    if (status === "בטיפול רפרנט") {
      dbUpdates.referent_start_date = new Date().toISOString().split("T")[0];
    }
    if (status === "אושר והופץ") {
      dbUpdates.closed_date = new Date().toISOString().split("T")[0];
      dbUpdates.status = "approved";
    }
    if (status === "נדחה") {
      dbUpdates.closed_date = new Date().toISOString().split("T")[0];
      dbUpdates.status = "rejected";
    }
    // Clear the "current return state" fields when moving out of "ממתין להשלמת יוצר".
    // The return itself stays forever in lesson_workflow_events.
    if (status !== "ממתין להשלמת יוצר") {
      dbUpdates.return_reason = null;
      dbUpdates.return_date = null;
      dbUpdates.returned_by = null;
    }
    // Status change + history event are written atomically
    await applyWorkflowTransition({
      lessonId,
      eventType: eventType || eventTypeForStatus(status),
      statusAfter: status,
      lessonUpdates: dbUpdates,
      note: note || null,
    });

    setLessons((prev) => prev.map((l) => l.id === lessonId ? {
      ...l,
      workflowStatus: status,
      status: dbUpdates.status || l.status,
      referentStartDate: dbUpdates.referent_start_date || l.referentStartDate,
      closedDate: dbUpdates.closed_date || l.closedDate,
      returnReason: status !== "ממתין להשלמת יוצר" ? undefined : l.returnReason,
      returnDate: status !== "ממתין להשלמת יוצר" ? undefined : l.returnDate,
      returnedBy: status !== "ממתין להשלמת יוצר" ? undefined : l.returnedBy,
    } : l));

    const lesson = lessons.find((l) => l.id === lessonId);
    if (lesson) {
      if (status === "בטיפול רפרנט") {
        const referentIds = lesson.distributedToReferents || [];
        referentIds.forEach((rid) => {
          addNotification(rid, "new_lesson", "לקח חדש לטיפולך", `הלקח "${lesson.title}" הועבר לטיפולך`, lessonId, "lesson");
        });
        // Notify creator
        addNotification(lesson.createdBy, "distributed", "הלקח הועבר לרפרנט", `הלקח "${lesson.title}" הועבר לטיפול רפרנט`, lessonId, "lesson");
      }
      if (status === "ממתין לאישור מנהל מערכת") {
        users.filter((u) => u.role === "admin").forEach((admin) => {
          addNotification(admin.id, "new_lesson", "לקח ממתין לאישור", `הלקח "${lesson.title}" ממתין לאישור מנהל מערכת`, lessonId, "lesson");
        });
      }
      if (status === "אושר והופץ") {
        addNotification(lesson.createdBy, "approved", "הלקח שלך אושר והופץ", `הלקח "${lesson.title}" אושר והופץ`, lessonId, "lesson");
      }
      if (status === "נדחה") {
        addNotification(lesson.createdBy, "rejected", "הלקח שלך נדחה", `הלקח "${lesson.title}" נדחה`, lessonId, "lesson");
      }

      void logActivity({
        actionType: "update",
        entityType: "lesson",
        entityId: lesson.id,
        objectLabel: `לקח #${lesson.id}`,
        description: note
          ? `שינה סטטוס טיפול ל-${status} עבור ${lesson.title}. הערות: ${note}`
          : `שינה סטטוס טיפול ל-${status} עבור ${lesson.title}`,
      });
    }
  }, [lessons, users, addNotification, logActivity, applyWorkflowTransition]);


  const returnLessonToCreator = useCallback(async (lessonId: number, reason: string, customReason?: string) => {
    const returnDate = new Date().toISOString().split("T")[0];
    const finalReason = reason === "אחר" && customReason ? customReason : reason;
    // Track who requested the return: referent or admin
    const returnedBy = currentUser.role === "referent" ? "referent" : "admin";
    const dbUpdates: any = {
      workflow_status: "ממתין להשלמת יוצר",
      return_reason: finalReason,
      return_date: returnDate,
      returned_by: returnedBy,
    };
    const lesson = lessons.find((l) => l.id === lessonId);
    // Status change + permanent history entry in one atomic call
    await applyWorkflowTransition({
      lessonId,
      eventType: WORKFLOW_EVENT_TYPES.RETURNED_TO_CREATOR,
      statusAfter: "ממתין להשלמת יוצר",
      lessonUpdates: dbUpdates,
      note: finalReason,
      targetUserId: lesson?.createdBy ?? null,
      targetUserName: users.find((u) => u.id === lesson?.createdBy)?.name ?? null,
    });
    setLessons((prev) => prev.map((l) => l.id === lessonId ? {
      ...l,
      workflowStatus: "ממתין להשלמת יוצר",
      returnReason: finalReason,
      returnDate,
      returnedBy,
    } : l));

    if (lesson) {
      addNotification(lesson.createdBy, "rejected", "נדרש מידע נוסף", `הלקח "${lesson.title}" הוחזר אליך להשלמה. סיבה: ${finalReason}`, lessonId, "lesson");
      void logActivity({
        actionType: "update",
        entityType: "lesson",
        entityId: lesson.id,
        objectLabel: `לקח #${lesson.id}`,
        description: `החזיר לקח ליוצר להשלמה: ${lesson.title}. סיבה: ${finalReason}`,
      });
    }
  }, [lessons, users, addNotification, logActivity, currentUser.role, applyWorkflowTransition]);


  const updateReferentDecision = useCallback(async (reviewId: number, decision: string, responseText: string) => {
    const respondedAt = new Date().toISOString();
    await supabase.from("referent_reviews" as any).update({
      decision,
      response_text: responseText,
      responded_at: respondedAt,
    }).eq("id", reviewId);

    setReferentReviews((prev) =>
      prev.map((r) => r.id === reviewId ? { ...r, decision, responseText, respondedAt } : r)
    );

    const review = referentReviews.find((r) => r.id === reviewId);
    if (review) {
      const lesson = lessons.find((l) => l.id === review.lessonId);
      // Every referent response is kept as its own history entry (never overwritten)
      void recordWorkflowEvent({
        lessonId: review.lessonId,
        eventType: WORKFLOW_EVENT_TYPES.REFERENT_RESPONSE,
        statusAfter: lesson?.workflowStatus ?? null,
        note: responseText ? `החלטה: ${decision}. ${responseText}` : `החלטה: ${decision}`,
      });
      users.filter((u) => u.role === "admin").forEach((admin) => {
        addNotification(admin.id, "ai_suggestion", "החלטת רפרנט התקבלה", `${currentUser.name} קבע: "${decision}" על "${lesson?.title || ""}"`, review.lessonId, "lesson");
      });
    }
  }, [currentUser.id, currentUser.name, referentReviews, lessons, users, addNotification, recordWorkflowEvent]);


  const addLessonCategory = useCallback(async (name: string) => {
    const { error } = await supabase.from("lesson_categories").insert({ name });
    if (error) throw error;
    setLessonCategories((prev) => [...prev, name]);
    void logActivity({
      actionType: "create",
      entityType: "category",
      entityId: name,
      objectLabel: name,
      description: `יצר קטגוריית לקחים: ${name}`,
    });
  }, [logActivity]);

  const renameLessonCategory = useCallback(async (oldName: string, newName: string) => {
    await supabase.from("lesson_categories").update({ name: newName }).eq("name", oldName);
    setLessonCategories((prev) => prev.map((c) => (c === oldName ? newName : c)));
    await supabase.from("lessons").update({ category: newName }).eq("category", oldName);
    setLessons((prev) => prev.map((l) => (l.category === oldName ? { ...l, category: newName } : l)));
    void logActivity({
      actionType: "update",
      entityType: "category",
      entityId: oldName,
      objectLabel: newName,
      description: `שינה קטגוריית לקחים מ-${oldName} ל-${newName}`,
    });
  }, [logActivity]);

  const deleteLessonCategory = useCallback(async (name: string) => {
    await supabase.from("lesson_categories").delete().eq("name", name);
    setLessonCategories((prev) => prev.filter((c) => c !== name));
    void logActivity({
      actionType: "delete",
      entityType: "category",
      entityId: name,
      objectLabel: name,
      description: `מחק קטגוריית לקחים: ${name}`,
    });
  }, [logActivity]);

  // Act as yourself or as a referent group you belong to — never as anyone else.
  const switchIdentity = useCallback((id: string) => {
    const target = identities.find((u) => u.id === id);
    if (!target) return;
    setCurrentUser(target);
    try { sessionStorage.setItem(ACT_AS_KEY, id); } catch { /* private mode */ }
  }, [identities]);

  const logout = useCallback(() => {
    void logActivity({
      actionType: "logout",
      entityType: "session",
      objectLabel: "יציאה מהמערכת",
      description: "התנתק מהמערכת",
    });
    try { sessionStorage.removeItem(ACT_AS_KEY); } catch { /* private mode */ }
    // Logging out of the module is logging out of Shan-AI.
    window.location.href = "/logout";
  }, [logActivity]);

  const addFeedback = useCallback(async (fb: { type: string; title: string; description: string }) => {
    const { data } = await supabase.from("feedback").insert({
      user_id: currentUser.id,
      user_name: currentUser.name,
      type: fb.type,
      title: fb.title,
      description: fb.description,
    }).select().single();
    if (data) setFeedbacks((prev) => [mapDbFeedback(data), ...prev]);

    // Notify all admins about the new feedback
    const typeLabel = feedbackTypeLabels[fb.type] || fb.type;
    users.filter((u) => u.role === "admin").forEach((admin) => {
      addNotification(admin.id, "ai_suggestion", "פידבק חדש התקבל", `${currentUser.name} שלח פידבק (${typeLabel}): ${fb.title}`, data?.id, "feedback");
    });
  }, [currentUser, users, addNotification]);

  const updateFeedbackStatus = useCallback(async (id: number, status: string, adminNotes?: string) => {
    const updates: any = { status };
    if (adminNotes !== undefined) updates.admin_notes = adminNotes;
    if (status === "closed") updates.closed_at = new Date().toISOString();
    await supabase.from("feedback").update(updates).eq("id", id);
    setFeedbacks((prev) => prev.map((f) => f.id === id ? { ...f, status: status as Feedback["status"], adminNotes: adminNotes ?? f.adminNotes, closedAt: status === "closed" ? new Date().toISOString() : f.closedAt } : f));

    // Send email notification to the feedback author
    const feedback = feedbacks.find((f) => f.id === id);
    if (feedback) {
      const user = users.find((u) => u.id === feedback.userId);
      if (user?.email) {
        const statusLabel = status === "closed" ? "נסגר" : status === "in_progress" ? "בטיפול" : status;
        const notesSection = adminNotes ? `<p><strong>הערת מנהל:</strong> ${adminNotes}</p>` : "";
        try {
          await supabase.functions.invoke("send-notification-email", {
            body: {
              to: user.email,
              subject: `עדכון פידבק: ${feedback.title}`,
              body: `
                <p>שלום ${user.name},</p>
                <p>הפידבק שלך <strong>"${feedback.title}"</strong> עודכן לסטטוס: <strong>${statusLabel}</strong></p>
                ${notesSection}
                <p>תודה על הפידבק!</p>
              `,
            },
          });
        } catch (e) {
          console.error("Failed to send feedback email:", e);
        }
      }
    }
  }, [feedbacks, users]);

  const deleteFeedback = useCallback(async (id: number) => {
    await supabase.from("feedback").delete().eq("id", id);
    setFeedbacks((prev) => prev.filter((f) => f.id !== id));
  }, []);

  return (
    <AppContext.Provider
      value={{
        currentUser,
        setCurrentUser,
        isLoggedIn,
        identities,
        switchIdentity,
        logout,
        hasPermission,
        users,
        updateUser,
        addUser,
        deleteUser,
        updateEmailPreferences,
        projects,
        addProject,
        deleteProject,
        updateProject,
        updateProjectStage,
        updateProjectStages,
        updateProjectEquipment,
        equipment,
        addEquipment,
        updateEquipment,
        removeEquipment,
        renameCategory,
        deleteCategory,
        lessons,
        addLesson,
        approveLesson,
        rejectLesson,
        closeLesson,
        distributeLesson,
        updateLessonAIReview,
        updateLesson,
        updateWorkflowStatus,
        returnLessonToCreator,
        updateReferentDecision,
        fetchLessonWorkflowEvents,
        workflowEventsVersion,

        deleteLesson,
        removeLessonFromProject,
        notifications,
        unreadCount,
        markNotificationsAsRead,
        markNotificationsAsReadByIds,
        markSingleNotificationAsRead,
        clearNotifications,
        implementations,
        referentReviews,
        analyzeRelevantLessons,
        fetchRelevantLessonSuggestions,
        addSuggestedLessonsToProject,
        respondToImplementation,
        respondToReferentReview,
        isAnalyzing,
        isLoading,
        lessonCategories,
        addLessonCategory,
        renameLessonCategory,
        deleteLessonCategory,
        feedbacks,
        addFeedback,
        updateFeedbackStatus,
        deleteFeedback,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useUser = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useUser must be used within UserProvider");
  return ctx;
};
