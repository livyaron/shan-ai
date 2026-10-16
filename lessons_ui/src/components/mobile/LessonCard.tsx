import { Link } from "react-router-dom";
import type { Lesson } from "@/context/UserContext";
import { riskColors, riskLabelsShort, statusLabels } from "@/lib/constants";
import { cn } from "@/lib/utils";
import { ilDate } from "@/lib/ilDate";

/** One lesson as a tappable card on the phone (PLAN-lessons-mobile.md M2). */
const LessonCard = ({ lesson, action }: { lesson: Lesson; action?: string }) => (
  <Link
    to={`/m/lessons/${lesson.id}`}
    className="block rounded-xl border border-border bg-card p-4 space-y-2 active:bg-muted"
  >
    <div className="flex items-start justify-between gap-2">
      <p className="font-medium leading-snug">{lesson.title || "(ללא כותרת)"}</p>
      <span className={cn("shrink-0 text-xs rounded-full border px-2 py-0.5", riskColors[lesson.risk])}>
        {riskLabelsShort[lesson.risk] ?? lesson.risk}
      </span>
    </div>
    <p className="text-xs text-muted-foreground truncate">
      {lesson.project || "ללא פרויקט"}
      {lesson.stage ? ` · ${lesson.stage}` : ""}
    </p>
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-muted-foreground">
        {statusLabels[lesson.status] ?? lesson.status} · {ilDate(lesson.date)}
      </span>
      {action && <span className="rounded-full bg-warning/15 text-warning px-2 py-0.5 font-medium">{action}</span>}
    </div>
  </Link>
);

export default LessonCard;
