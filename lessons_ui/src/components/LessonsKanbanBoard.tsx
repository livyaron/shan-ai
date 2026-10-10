import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { BookOpen, Clock, User } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useUser } from "@/context/UserContext";
import type { Lesson } from "@/context/UserContext";
import {
  workflowStatusColors,
  calculateSlaStatus,
  slaStatusColors,
  SLA_STATUS,
  ACTIVE_WORKFLOW_STATUSES,
} from "@/lib/constants";

interface KanbanBoardProps {
  lessons: Lesson[];
  currentUserId: string;
  currentUserRole: string;
}

/**
 * Kanban board showing active lessons grouped by workflow status columns.
 * Clicking a card navigates to the lesson detail page.
 */
const LessonsKanbanBoard = ({ lessons, currentUserId, currentUserRole }: KanbanBoardProps) => {
  const navigate = useNavigate();
  const { users } = useUser();

  // Group lessons by workflow status
  const columns = useMemo(() => {
    const grouped: Record<string, Lesson[]> = {};
    ACTIVE_WORKFLOW_STATUSES.forEach((status) => {
      grouped[status] = [];
    });
    lessons.forEach((lesson) => {
      if (ACTIVE_WORKFLOW_STATUSES.includes(lesson.workflowStatus)) {
        grouped[lesson.workflowStatus].push(lesson);
      }
    });
    return grouped;
  }, [lessons]);

  const getUserName = (id: string | undefined) => {
    if (!id) return "—";
    return users.find((u) => u.id === id)?.name || id;
  };

  const formatDate = (dateStr: string | undefined) => {
    if (!dateStr) return "—";
    try {
      return new Date(dateStr).toLocaleDateString("he-IL");
    } catch {
      return dateStr;
    }
  };

  const needsMyAction = (lesson: Lesson): boolean => {
    if (currentUserRole === "admin" && lesson.workflowStatus === "ממתין לאישור מנהל מערכת") return true;
    if (lesson.createdBy === currentUserId && lesson.workflowStatus === "ממתין להשלמת יוצר") return true;
    if (lesson.assignedTo === currentUserId && lesson.workflowStatus === "בטיפול רפרנט") return true;
    return false;
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4" dir="rtl">
      {ACTIVE_WORKFLOW_STATUSES.map((status) => (
        <div key={status} className="space-y-3">
          {/* Column header */}
          <div className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={workflowStatusColors[status]}>
                {status}
              </Badge>
            </div>
            <span className="text-xs text-muted-foreground font-medium">
              {columns[status].length}
            </span>
          </div>

          {/* Cards */}
          <div className="space-y-2 min-h-[100px]">
            {columns[status].map((lesson) => {
              const sla = calculateSlaStatus(lesson.referentStartDate);
              const isActionRequired = needsMyAction(lesson);

              return (
                <Card
                  key={lesson.id}
                  className={`cursor-pointer hover:shadow-md transition-shadow ${
                    isActionRequired ? "border-r-4 border-r-warning bg-warning/[0.03]" : "border-0"
                  }`}
                  onClick={() => navigate(`/lessons/${lesson.id}`)}
                >
                  <CardContent className="p-3 space-y-2">
                    {/* Lesson ID + Title */}
                    <div className="flex items-start gap-2">
                      <span className="text-[10px] text-muted-foreground font-mono shrink-0 mt-0.5">
                        #{lesson.id}
                      </span>
                      <h4 className="text-sm font-medium leading-tight line-clamp-2">
                        {lesson.title}
                      </h4>
                    </div>

                    {/* Project */}
                    <p className="text-xs text-muted-foreground truncate">
                      {lesson.project}
                    </p>

                    {/* Referent */}
                    {lesson.assignedTo && (
                      <div className="flex items-center gap-1 text-xs text-muted-foreground">
                        <User className="w-3 h-3" />
                        <span className="truncate">{getUserName(lesson.assignedTo)}</span>
                      </div>
                    )}

                    {/* Dates */}
                    <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                      <span>נוצר: {formatDate(lesson.date)}</span>
                      {lesson.updatedAt && (
                        <span>עודכן: {formatDate(lesson.updatedAt)}</span>
                      )}
                    </div>

                    {/* SLA indicator */}
                    {lesson.referentStartDate && lesson.workflowStatus !== "חדש" && (
                      <div className="flex items-center gap-1.5">
                        <Clock className={`w-3 h-3 ${
                          sla.status === SLA_STATUS.OVERDUE ? "text-destructive" :
                          sla.status === SLA_STATUS.WARNING ? "text-warning" : "text-success"
                        }`} />
                        <Badge variant="outline" className={`text-[10px] py-0 ${slaStatusColors[sla.status]}`}>
                          {sla.status} ({sla.days} ימים)
                        </Badge>
                      </div>
                    )}

                    {/* Return reason for creator */}
                    {lesson.workflowStatus === "ממתין להשלמת יוצר" && lesson.returnReason && (
                      <div className="p-1.5 rounded bg-accent/10 border border-accent/20 text-[10px] text-accent">
                        סיבת החזרה: {lesson.returnReason}
                      </div>
                    )}

                    {/* Action required badge */}
                    {isActionRequired && (
                      <Badge variant="outline" className="text-[10px] py-0 bg-warning/15 text-warning border-warning/30 w-full justify-center">
                        נדרשת פעולה ממך
                      </Badge>
                    )}
                  </CardContent>
                </Card>
              );
            })}
            {columns[status].length === 0 && (
              <div className="text-center py-6 text-xs text-muted-foreground">
                אין לקחים
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

export default LessonsKanbanBoard;
