import { CheckCircle, Circle, Clock, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { WORKFLOW_STATUSES, FINAL_WORKFLOW_STATUSES, calculateSlaStatus, slaStatusColors, SLA_STATUS } from "@/lib/constants";
import type { Lesson } from "@/context/UserContext";
import type { MockUser } from "@/context/UserContext";

// Timeline steps in order
const TIMELINE_STEPS = [
  { key: "חדש", label: "נוצר" },
  { key: "בטיפול רפרנט", label: "הועבר לרפרנט" },
  { key: "ממתין להשלמת יוצר", label: "ממתין להשלמת יוצר" },
  { key: "ממתין לאישור מנהל מערכת", label: "ממתין לאישור מנהל מערכת" },
  { key: "אושר והופץ", label: "אושר והופץ" },
  { key: "נדחה", label: "נדחה" },
];

interface LessonTimelineProps {
  lesson: Lesson;
  users: MockUser[];
}

/** Visual progress timeline for a lesson's workflow stages */
const LessonTimeline = ({ lesson, users }: LessonTimelineProps) => {
  const currentStatus = lesson.workflowStatus;

  // Determine which steps to show based on final status
  const isFinal = FINAL_WORKFLOW_STATUSES.includes(currentStatus);
  const isRejected = currentStatus === "נדחה";
  const isApproved = currentStatus === "אושר והופץ";

  // Build active path: always show first 4 steps, then the relevant final step
  const activeSteps = TIMELINE_STEPS.slice(0, 4);
  if (isRejected) {
    activeSteps.push(TIMELINE_STEPS.find((s) => s.key === "נדחה")!);
  } else if (isApproved || isFinal) {
    activeSteps.push(TIMELINE_STEPS.find((s) => s.key === "אושר והופץ")!);
  }

  // Find the index of the current status in our active steps
  const currentIndex = activeSteps.findIndex((s) => s.key === currentStatus);

  // Determine the handler name
  const assignedUser = lesson.assignedTo ? users.find((u) => u.id === lesson.assignedTo) : null;
  const creatorUser = users.find((u) => u.id === lesson.createdBy);

  // SLA info
  const sla = calculateSlaStatus(lesson.referentStartDate);

  return (
    <div className="space-y-4">
      {/* Timeline visual */}
      <div className="flex items-start gap-0 overflow-x-auto pb-2">
        {activeSteps.map((step, index) => {
          const isCompleted = index < currentIndex;
          const isCurrent = index === currentIndex;
          const isFutureStep = index > currentIndex;

          return (
            <div key={step.key} className="flex items-center flex-1 min-w-0">
              <div className="flex flex-col items-center gap-1.5 min-w-[60px]">
                <div
                  className={cn(
                    "w-8 h-8 rounded-full flex items-center justify-center border-2 transition-colors",
                    isCompleted && "bg-success border-success text-success-foreground",
                    isCurrent && step.key === "נדחה" && "bg-destructive border-destructive text-destructive-foreground",
                    isCurrent && step.key !== "נדחה" && "bg-primary border-primary text-primary-foreground",
                    isFutureStep && "bg-muted border-muted-foreground/20 text-muted-foreground"
                  )}
                >
                  {isCompleted ? (
                    <CheckCircle className="w-4 h-4" />
                  ) : isCurrent ? (
                    <Clock className="w-4 h-4" />
                  ) : (
                    <Circle className="w-4 h-4" />
                  )}
                </div>
                <span
                  className={cn(
                    "text-[10px] text-center leading-tight max-w-[80px]",
                    isCurrent ? "font-semibold text-foreground" : "text-muted-foreground"
                  )}
                >
                  {step.label}
                </span>
              </div>
              {/* Connector line */}
              {index < activeSteps.length - 1 && (
                <div
                  className={cn(
                    "h-0.5 flex-1 min-w-[16px] mt-4",
                    index < currentIndex ? "bg-success" : "bg-muted-foreground/20"
                  )}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Current status details */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div>
          <span className="text-muted-foreground text-xs">סטטוס נוכחי</span>
          <p className="font-medium">{currentStatus}</p>
        </div>
        <div>
          <span className="text-muted-foreground text-xs">נמצא אצל</span>
          <p className="font-medium">
            {currentStatus === "חדש" && "ממתין לשיוך"}
            {currentStatus === "בטיפול רפרנט" && (assignedUser?.name || "רפרנט")}
            {currentStatus === "ממתין להשלמת יוצר" && (creatorUser?.name || "יוצר הלקח")}
            {currentStatus === "ממתין לאישור מנהל מערכת" && "מנהל מערכת"}
            {currentStatus === "אושר והופץ" && "הושלם"}
            {currentStatus === "נדחה" && "נדחה"}
          </p>
        </div>
        {lesson.referentStartDate && !isFinal && (
          <div>
            <span className="text-muted-foreground text-xs">ימים בטיפול</span>
            <p className="font-medium">{sla.days} ימים</p>
          </div>
        )}
        {lesson.referentStartDate && !isFinal && (
          <div>
            <span className="text-muted-foreground text-xs">מצב SLA</span>
            <div className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium mt-0.5", slaStatusColors[sla.status])}>
              {sla.status === SLA_STATUS.OVERDUE && <AlertTriangle className="w-3 h-3" />}
              {sla.status === SLA_STATUS.WARNING && <Clock className="w-3 h-3" />}
              {sla.status}
            </div>
          </div>
        )}
      </div>

      {/* Return reason banner */}
      {lesson.workflowStatus === "ממתין להשלמת יוצר" && lesson.returnReason && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-warning/10 border border-warning/20">
          <AlertTriangle className="w-4 h-4 text-warning mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-medium text-warning">נדרשת פעולה מיוצר הלקח</p>
            <p className="text-xs text-muted-foreground mt-0.5">סיבת החזרה: {lesson.returnReason}</p>
            {lesson.returnDate && (
              <p className="text-xs text-muted-foreground">תאריך החזרה: {lesson.returnDate}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default LessonTimeline;
