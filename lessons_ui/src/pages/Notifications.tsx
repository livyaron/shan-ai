import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell, BookOpen, FolderKanban, CheckCircle2, Send, XCircle, Settings, Mail,
  CheckCheck, Trash2, AlertTriangle, Clock, MessageSquare,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { useUser } from "@/context/UserContext";
import { emailPreferenceLabels } from "@/context/UserContext";
import type { EmailPreferences, Notification } from "@/context/UserContext";
import type { LucideIcon } from "lucide-react";
import { toast } from "@/hooks/use-toast";
import { buildFeedbackDrilldownPath } from "@/lib/drilldown";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

// Icon and color maps for notification types
const iconMap: Record<string, LucideIcon> = {
  new_lesson: BookOpen,
  approved: CheckCircle2,
  rejected: XCircle,
  distributed: Send,
  stage_change: FolderKanban,
  ai_suggestion: Settings,
};

const colorMap: Record<string, string> = {
  new_lesson: "bg-info",
  approved: "bg-success",
  rejected: "bg-destructive",
  distributed: "bg-primary",
  stage_change: "bg-accent",
  ai_suggestion: "bg-accent",
};

// Human-readable type labels in Hebrew
const typeLabels: Record<string, string> = {
  new_lesson: "לקח",
  approved: "אישור",
  rejected: "דחייה / השלמה",
  distributed: "הפצה",
  stage_change: "שינוי בפרויקט",
  ai_suggestion: "AI / פידבק",
};

const formatRelativeTime = (timeStr: string): string => {
  if (!timeStr || timeStr === "עכשיו") return "עכשיו";
  const date = new Date(timeStr);
  if (isNaN(date.getTime())) return timeStr;
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffMin < 1) return "עכשיו";
  if (diffMin < 60) return `לפני ${diffMin} דקות`;
  if (diffHours < 24) return `לפני ${diffHours} שעות`;
  if (diffDays < 7) return `לפני ${diffDays} ימים`;
  return date.toLocaleDateString("he-IL");
};

const normalizeText = (value: string) =>
  value.replace(/["״']/g, "").replace(/\s+/g, " ").trim().toLowerCase();

const extractQuotedText = (value: string) =>
  value.match(/"([^"]+)"/)?.[1] || value.match(/״([^״]+)״/)?.[1] || null;

const getNumericEntityId = (value?: string | null): number | null => {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : null;
};

const findLegacyLessonId = (n: Notification, lessons: Array<{ id: number; title: string }>): number | null => {
  const candidates = [
    extractQuotedText(n.description),
    extractQuotedText(n.title),
    n.description.split("—")[0]?.trim(),
    n.description.split(":").slice(1).join(":").trim(),
  ]
    .filter(Boolean)
    .map((candidate) => normalizeText(candidate as string));

  const normalizedDescription = normalizeText(n.description);
  const lesson = lessons.find((l) => {
    const normalizedTitle = normalizeText(l.title);
    return candidates.includes(normalizedTitle) || normalizedDescription.includes(normalizedTitle);
  });

  return lesson?.id ?? null;
};

const findLegacyFeedbackId = (n: Notification, feedbacks: Array<{ id: number; title: string }>): number | null => {
  const candidates = [
    extractQuotedText(n.description),
    extractQuotedText(n.title),
    n.description.split(":").slice(1).join(":").trim(),
    n.title,
  ]
    .filter(Boolean)
    .map((candidate) => normalizeText(candidate as string));

  const normalizedDescription = normalizeText(n.description);
  const feedback = feedbacks.find((item) => {
    const normalizedTitle = normalizeText(item.title);
    return candidates.includes(normalizedTitle) || normalizedDescription.includes(normalizedTitle);
  });

  return feedback?.id ?? null;
};

/** Determine navigation path from notification */
const getEntityPath = (
  n: Notification,
  lessons: Array<{ id: number; title: string }>,
  projects: Array<{ id: number; name: string }>,
  feedbacks: Array<{ id: number; title: string }>,
): string | null => {
  const entityId = getNumericEntityId(n.entityId);

  if (n.entityType === "lesson") {
    if (entityId) return `/lessons/${entityId}`;
    const legacyLessonId = findLegacyLessonId(n, lessons);
    if (legacyLessonId) return `/lessons/${legacyLessonId}`;
  }

  if (n.entityType === "project") {
    if (entityId) return `/projects/${entityId}`;
  }

  if (n.entityType === "feedback") {
    if (entityId) return buildFeedbackDrilldownPath(entityId);

    const legacyFeedbackId = findLegacyFeedbackId(n, feedbacks);
    if (legacyFeedbackId) return buildFeedbackDrilldownPath(legacyFeedbackId);

    const legacyLessonId = findLegacyLessonId(n, lessons);
    if (legacyLessonId) return `/lessons/${legacyLessonId}`;
  }

  if (entityId) {
    if (["new_lesson", "approved", "rejected", "distributed", "ai_suggestion"].includes(n.type)) {
      return `/lessons/${entityId}`;
    }
    if (n.type === "stage_change") {
      return `/projects/${entityId}`;
    }
  }

  if (n.title.includes("פידבק")) {
    const legacyFeedbackId = findLegacyFeedbackId(n, feedbacks);
    if (legacyFeedbackId) return buildFeedbackDrilldownPath(legacyFeedbackId);

    const legacyLessonId = findLegacyLessonId(n, lessons);
    if (legacyLessonId) return `/lessons/${legacyLessonId}`;
  }

  const legacyLessonId = findLegacyLessonId(n, lessons);
  if (legacyLessonId) return `/lessons/${legacyLessonId}`;

  const legacyProject = projects.find((p) => n.description.includes(p.name));
  if (legacyProject) return `/projects/${legacyProject.id}`;

  return null;
};

const Notifications = () => {
  const navigate = useNavigate();
  const {
    currentUser, notifications, lessons, projects, feedbacks, updateEmailPreferences,
    markNotificationsAsRead, markNotificationsAsReadByIds,
    markSingleNotificationAsRead, clearNotifications,
  } = useUser();

  const [showPrefs, setShowPrefs] = useState(false);
  const [showClearDialog, setShowClearDialog] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Filter and sort: newest first
  const userNotifs = useMemo(() => {
    return notifications
      .filter((n) => n.userId === currentUser.id)
      .sort((a, b) => {
        const aTime = new Date(a.time).getTime();
        const bTime = new Date(b.time).getTime();
        if (isNaN(aTime) || isNaN(bTime)) return 0;
        return bTime - aTime;
      });
  }, [notifications, currentUser.id]);

  const unreadCount = userNotifs.filter((n) => !n.read).length;

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === userNotifs.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(userNotifs.map((n) => n.id)));
    }
  };

  const handleMarkSelectedAsRead = async () => {
    const unreadSelected = [...selectedIds].filter((id) => {
      const n = userNotifs.find((notif) => notif.id === id);
      return n && !n.read;
    });
    if (unreadSelected.length === 0) {
      toast({ title: "כל ההתראות שנבחרו כבר נקראו" });
      return;
    }
    await markNotificationsAsReadByIds(unreadSelected);
    toast({ title: `${unreadSelected.length} התראות סומנו כנקראו ✓` });
    setSelectedIds(new Set());
  };

  const handleNotificationClick = async (n: Notification) => {
    // Mark as read
    if (!n.read) {
      await markSingleNotificationAsRead(n.id);
    }
    // Navigate to entity
    const path = getEntityPath(n, lessons, projects, feedbacks);
    if (path) {
      navigate(path);
    }
  };

  const handleTogglePref = async (key: keyof EmailPreferences) => {
    const newPrefs = { ...currentUser.emailPreferences, [key]: !currentUser.emailPreferences[key] };
    await updateEmailPreferences(currentUser.id, newPrefs);
    toast({ title: "העדפות מייל עודכנו ✓" });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-heading font-bold">התראות</h1>
          <p className="text-muted-foreground mt-1">
            {userNotifs.length > 0 ? `${userNotifs.length} עדכונים` : "אין התראות"}
            {unreadCount > 0 && ` · ${unreadCount} לא נקראו`}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {selectedIds.size > 0 && (
            <button
              onClick={handleMarkSelectedAsRead}
              className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors px-3 py-2 rounded-lg hover:bg-muted"
            >
              <CheckCheck className="w-4 h-4" />
              סמן {selectedIds.size} כנקראו
            </button>
          )}
          {unreadCount > 0 && selectedIds.size === 0 && (
            <button
              onClick={() => markNotificationsAsRead()}
              className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors px-3 py-2 rounded-lg hover:bg-muted"
            >
              <CheckCheck className="w-4 h-4" />
              סמן הכל כנקרא
            </button>
          )}
          {userNotifs.length > 0 && (
            <button
              onClick={() => setShowClearDialog(true)}
              className="flex items-center gap-2 text-sm text-destructive hover:text-destructive/80 transition-colors px-3 py-2 rounded-lg hover:bg-destructive/10"
            >
              <Trash2 className="w-4 h-4" />
              נקה התראות
            </button>
          )}
          <button
            onClick={() => setShowPrefs(!showPrefs)}
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors px-3 py-2 rounded-lg hover:bg-muted"
          >
            <Mail className="w-4 h-4" />
            העדפות מייל
          </button>
        </div>
      </div>

      {/* Email preferences panel */}
      {showPrefs && (
        <Card className="border-0 shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-heading flex items-center gap-2">
              <Mail className="w-4 h-4" />
              העדפות התראות מייל
            </CardTitle>
            <p className="text-xs text-muted-foreground">בחר אילו התראות תרצה לקבל גם במייל</p>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {(Object.keys(emailPreferenceLabels) as (keyof EmailPreferences)[]).map((key) => (
                <div key={key} className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className={`w-8 h-8 rounded-lg ${colorMap[key] || "bg-muted"} flex items-center justify-center`}>
                      {(() => {
                        const Icon = iconMap[key] || Bell;
                        return <Icon className="w-4 h-4 text-primary-foreground" />;
                      })()}
                    </div>
                    <Label htmlFor={`pref-${key}`} className="text-sm cursor-pointer">
                      {emailPreferenceLabels[key]}
                    </Label>
                  </div>
                  <Switch
                    id={`pref-${key}`}
                    checked={currentUser.emailPreferences[key]}
                    onCheckedChange={() => handleTogglePref(key)}
                  />
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Select all checkbox */}
      {userNotifs.length > 0 && (
        <div className="flex items-center gap-3 px-2">
          <Checkbox
            checked={selectedIds.size === userNotifs.length && userNotifs.length > 0}
            onCheckedChange={toggleSelectAll}
          />
          <span className="text-xs text-muted-foreground">בחר הכל</span>
        </div>
      )}

      {/* Notification list */}
      <div className="space-y-3">
        {userNotifs.map((n) => {
          const Icon = iconMap[n.type] || Bell;
          const color = colorMap[n.type] || "bg-muted";
          const typeLabel = typeLabels[n.type] || n.type;
          const entityPath = getEntityPath(n, lessons, projects, feedbacks);
          const isSelected = selectedIds.has(n.id);

          return (
            <Card
              key={n.id}
              className={`border-0 shadow-sm transition-all ${
                !n.read ? "bg-accent/5 ring-1 ring-accent/30 shadow-md" : ""
              } ${entityPath ? "cursor-pointer hover:shadow-md" : ""}`}
              onClick={() => handleNotificationClick(n)}
            >
              <CardContent className="p-5 flex items-center gap-4">
                {/* Checkbox - stop propagation so clicking checkbox doesn't navigate */}
                <div onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    checked={isSelected}
                    onCheckedChange={() => toggleSelect(n.id)}
                  />
                </div>
                <div className={`w-10 h-10 rounded-xl ${color} flex items-center justify-center shrink-0`}>
                  <Icon className="w-5 h-5 text-primary-foreground" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className={`text-sm ${!n.read ? "font-bold" : "font-medium"}`}>{n.title}</h3>
                    {!n.read && (
                      <Badge variant="outline" className="text-[10px] bg-accent/10 text-accent border-accent/20">חדש</Badge>
                    )}
                    <Badge variant="outline" className="text-[10px]">{typeLabel}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5 truncate">{n.description}</p>
                </div>
                <span className="text-xs text-muted-foreground whitespace-nowrap">{formatRelativeTime(n.time)}</span>
              </CardContent>
            </Card>
          );
        })}
        {userNotifs.length === 0 && (
          <p className="text-muted-foreground text-center py-12">אין התראות להצגה</p>
        )}
      </div>

      {/* Clear all confirmation dialog */}
      <AlertDialog open={showClearDialog} onOpenChange={setShowClearDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>מחיקת כל ההתראות</AlertDialogTitle>
            <AlertDialogDescription>
              האם למחוק את כל ההתראות שלך? פעולה זו אינה ניתנת לביטול.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>ביטול</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                await clearNotifications();
                toast({ title: "כל ההתראות נמחקו ✓" });
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              מחק הכל
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default Notifications;
