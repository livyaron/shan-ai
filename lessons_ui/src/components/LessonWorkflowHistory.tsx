import { useCallback, useEffect, useMemo, useState } from "react";
import { History, ArrowUpDown, Loader2, MessageSquare, ArrowLeft } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useUser, roleLabels, UserRole } from "@/context/UserContext";
import {
  LessonWorkflowEvent,
  formatWorkflowEventDateTime,
  getWorkflowActorRoleStyle,
  workflowEventLabels,
} from "@/lib/lessonWorkflowHistory";
import { cn } from "@/lib/utils";

interface LessonWorkflowHistoryProps {
  lessonId: number;
}

/** Read-only chronological trail of every workflow event on a lesson */
const LessonWorkflowHistory = ({ lessonId }: LessonWorkflowHistoryProps) => {
  const { fetchLessonWorkflowEvents, workflowEventsVersion } = useUser();
  const [events, setEvents] = useState<LessonWorkflowEvent[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [newestFirst, setNewestFirst] = useState(true);

  const loadEvents = useCallback(async () => {
    setIsLoading(true);
    const data = await fetchLessonWorkflowEvents(lessonId);
    setEvents(data);
    setIsLoading(false);
  }, [fetchLessonWorkflowEvents, lessonId]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents, workflowEventsVersion]);

  const orderedEvents = useMemo(
    () => (newestFirst ? [...events].reverse() : events),
    [events, newestFirst]
  );

  return (
    <Card className="border-0 shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="flex items-center gap-2 text-base font-heading">
          <History className="w-5 h-5 text-primary" />
          היסטוריית טיפול
        </CardTitle>
        {events.length > 1 && (
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => setNewestFirst((v) => !v)}>
            <ArrowUpDown className="w-4 h-4" />
            {newestFirst ? "החדש ביותר למעלה" : "הישן ביותר למעלה"}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
            <Loader2 className="w-4 h-4 animate-spin" />
            טוען היסטוריית טיפול...
          </div>
        ) : orderedEvents.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">עדיין לא נרשמו אירועי טיפול עבור לקח זה.</p>
        ) : (
          <ol className="relative space-y-3 pe-4">
            {orderedEvents.map((event) => {
              const roleStyle = getWorkflowActorRoleStyle(event.actorRole);
              const statusChanged = event.statusBefore && event.statusAfter && event.statusBefore !== event.statusAfter;

              return (
                <li key={event.id} className="relative flex gap-3">
                  <div className="flex flex-col items-center pt-1.5">
                    <span className={cn("w-3 h-3 rounded-full shrink-0", roleStyle.dot)} />
                    <span className="flex-1 w-px bg-border mt-1" />
                  </div>
                  <div className="flex-1 rounded-lg border border-border bg-muted/30 p-3 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {workflowEventLabels[event.eventType] || event.eventType}
                      </span>
                      <Badge variant="outline" className={cn("text-[11px] font-normal", roleStyle.badge)}>
                        {roleLabels[event.actorRole as UserRole] || event.actorRole}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {event.actorName} · {formatWorkflowEventDateTime(event.createdAt)}
                      </span>
                    </div>

                    {statusChanged && (
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <span>{event.statusBefore}</span>
                        <ArrowLeft className="w-3 h-3" />
                        <span className="font-medium text-foreground">{event.statusAfter}</span>
                      </div>
                    )}

                    {event.targetUserName && (
                      <p className="text-xs text-muted-foreground">הועבר אל: {event.targetUserName}</p>
                    )}

                    {event.note && (
                      <div className="flex items-start gap-1.5 text-sm">
                        <MessageSquare className="w-3.5 h-3.5 mt-0.5 shrink-0 text-muted-foreground" />
                        <p className="text-foreground/90 whitespace-pre-wrap">{event.note}</p>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
};

export default LessonWorkflowHistory;
