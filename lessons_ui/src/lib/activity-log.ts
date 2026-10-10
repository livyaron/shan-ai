export interface ActivityLogEntry {
  id: number;
  userId: string;
  userName: string;
  userEmail: string;
  userRole: string;
  actionType: string;
  entityType: string;
  entityId?: string | null;
  objectLabel: string;
  description: string;
  createdAt: string;
}

export interface ActivityLogFilters {
  searchTerm: string;
  userId: string;
  actionType: string;
  entityType: string;
  startDate: string;
  endDate: string;
}

export const activityActionLabels: Record<string, string> = {
  login: "התחברות",
  logout: "התנתקות",
  create: "יצירה",
  update: "עדכון",
  delete: "מחיקה",
  password_change: "שינוי סיסמה",
};

export const activityEntityLabels: Record<string, string> = {
  session: "התחברות",
  lesson: "לקח",
  project: "פרויקט",
  user: "משתמש",
  equipment: "ציוד",
  category: "קטגוריה",
  system: "מערכת",
};

export const mapActivityLogRow = (row: any): ActivityLogEntry => ({
  id: row.id,
  userId: row.user_id,
  userName: row.user_name,
  userEmail: row.user_email,
  userRole: row.user_role,
  actionType: row.action_type,
  entityType: row.entity_type,
  entityId: row.entity_id,
  objectLabel: row.object_label || "",
  description: row.description,
  createdAt: row.created_at,
});

export const formatActivityDateTime = (value: string) => {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "—";
  }

  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const year = date.getFullYear();
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");

  return `${day}/${month}/${year} ${hours}:${minutes}`;
};

export const filterActivityLogs = (logs: ActivityLogEntry[], filters: ActivityLogFilters) => {
  const normalizedSearch = filters.searchTerm.trim().toLowerCase();

  return logs.filter((log) => {
    const createdDate = log.createdAt.slice(0, 10);
    const matchesStartDate = !filters.startDate || createdDate >= filters.startDate;
    const matchesEndDate = !filters.endDate || createdDate <= filters.endDate;
    const matchesUser = !filters.userId || log.userId === filters.userId;
    const matchesAction = !filters.actionType || log.actionType === filters.actionType;
    const matchesEntity = !filters.entityType || log.entityType === filters.entityType;
    const haystack = `${log.userName} ${log.userEmail} ${log.objectLabel} ${log.description}`.toLowerCase();
    const matchesSearch = !normalizedSearch || haystack.includes(normalizedSearch);

    return matchesStartDate && matchesEndDate && matchesUser && matchesAction && matchesEntity && matchesSearch;
  });
};