import { forwardRef } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Trophy, TrendingUp, TrendingDown, Star, Sparkles, Users, FileText,
  CheckCircle2, Clock, PenLine, ClipboardCheck, ArrowLeftCircle,
  Briefcase, UserCheck, Rocket, Lightbulb,
} from "lucide-react";
import type { NewsletterStats } from "@/lib/newsletterStats";
import { formatDateRange } from "@/lib/newsletterPeriods";
import { pickManagerialMessage, buildActionableInsights, INSIGHTS_CTA } from "@/lib/newsletterCopy";

interface Props {
  stats: NewsletterStats;
}

const ZoneTitle = ({ icon: Icon, children }: { icon: typeof Trophy; children: React.ReactNode }) => (
  <div className="flex items-center gap-2 mb-3">
    <Icon className="w-4 h-4 text-accent" />
    <h2 className="text-base font-heading font-bold">{children}</h2>
  </div>
);

const StatusPill = ({ icon: Icon, label, value }: { icon: typeof Trophy; label: string; value: number }) => (
  <div className="flex items-center gap-1.5 text-sm">
    <Icon className="w-3.5 h-3.5 text-muted-foreground" />
    <span className="font-semibold">{value}</span>
    <span className="text-muted-foreground">{label}</span>
  </div>
);

const KPI = ({ icon: Icon, label, value }: { icon: typeof Trophy; label: string; value: string | number }) => (
  <div className="flex flex-col items-center justify-center rounded-xl p-4 bg-primary/5">
    <Icon className="w-5 h-5 mb-1.5 text-primary" />
    <div className="text-2xl font-bold">{value}</div>
    <div className="text-xs text-muted-foreground text-center mt-1 leading-tight">{label}</div>
  </div>
);

// Distinct visual identity per leaders category
interface LeaderCategoryStyle {
  title: string;
  icon: typeof Trophy;
  bg: string;
  border: string;
  accentText: string;
  chip: string;
}

const LeadersColumn = ({
  style, items,
}: {
  style: LeaderCategoryStyle;
  items: { userId: string; name: string; roleLabel: string; count: number }[];
}) => {
  const Icon = style.icon;
  return (
    <div className={`rounded-xl border-2 ${style.border} ${style.bg} p-4 flex flex-col`}>
      <div className="flex items-center gap-2 mb-3">
        <div className={`w-8 h-8 rounded-lg ${style.chip} flex items-center justify-center`}>
          <Icon className="w-4 h-4" />
        </div>
        <div className={`text-sm font-bold ${style.accentText}`}>{style.title}</div>
      </div>
      {items.length > 0 ? (
        <ol className="space-y-2.5">
          {items.map((u, idx) => (
            <li key={u.userId} className="flex items-center gap-2">
              <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                idx === 0 ? style.chip : "bg-background/70 text-foreground"
              }`}>{idx + 1}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold truncate">{u.name}</div>
                <div className="text-[11px] text-muted-foreground truncate">{u.roleLabel}</div>
              </div>
              <span className={`text-lg font-bold ${style.accentText}`}>{u.count}</span>
            </li>
          ))}
        </ol>
      ) : (
        <div className="text-xs text-muted-foreground text-center py-6">אין נתונים בתקופה זו</div>
      )}
    </div>
  );
};


const NewsletterPreview = forwardRef<HTMLDivElement, Props>(({ stats }, ref) => {
  const {
    range, kpiCreated, createdApproved, createdPending, createdDraft,
    kpiApproved, kpiImplemented,
    topPMs, topReferents, topImplementers,
    featuredLesson, implementationRate, implementationRateDelta,
    createdPrev, createdDeltaPct,
  } = stats;

  const generatedAt = new Date().toLocaleDateString("he-IL");
  const managerMsg = pickManagerialMessage(range);
  const insights = buildActionableInsights(stats);

  const pmStyle: LeaderCategoryStyle = {
    title: "מנהלי פרויקטים",
    icon: Briefcase,
    bg: "bg-primary/5",
    border: "border-primary/25",
    accentText: "text-primary",
    chip: "bg-primary text-primary-foreground",
  };
  const refStyle: LeaderCategoryStyle = {
    title: "רפרנטים",
    icon: UserCheck,
    bg: "bg-accent/5",
    border: "border-accent/25",
    accentText: "text-accent",
    chip: "bg-accent text-accent-foreground",
  };
  const impStyle: LeaderCategoryStyle = {
    title: "יישום לקחים בפרויקטים",
    icon: Rocket,
    bg: "bg-emerald-500/5",
    border: "border-emerald-500/25",
    accentText: "text-emerald-700 dark:text-emerald-400",
    chip: "bg-emerald-500 text-white",
  };

  return (
    <div
      ref={ref}
      dir="rtl"
      className="bg-background text-foreground p-8 space-y-5 font-body"
      style={{ width: "794px", minHeight: "1123px" }}
    >
      {/* Header */}
      <div className="text-center border-b pb-4">
        <div className="text-xs text-muted-foreground mb-1">מערכת הלקחים · עלון פעילות</div>
        <h1 className="text-3xl font-heading font-bold text-primary">{range.labelHe}</h1>
        <div className="text-xs text-muted-foreground mt-1">
          {formatDateRange(range.start, range.end)} · הופק: {generatedAt}
        </div>
      </div>

      {/* ===== Zone 1 — מובילי התקופה (bold hero, ~half page) ===== */}
      <Card className="border-0 shadow-sm overflow-hidden">
        <CardContent className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <Trophy className="w-6 h-6 text-accent" />
            <h2 className="text-xl font-heading font-bold">מובילי התקופה</h2>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <LeadersColumn style={pmStyle} items={topPMs} />
            <LeadersColumn style={refStyle} items={topReferents} />
            <LeadersColumn style={impStyle} items={topImplementers} />
          </div>
        </CardContent>
      </Card>

      {/* ===== Zone 2 — מדדי פעילות ===== */}
      <Card className="border-0 shadow-sm">
        <CardContent className="p-4 space-y-3">
          <ZoneTitle icon={FileText}>מדדי פעילות</ZoneTitle>
          <div className="grid grid-cols-3 gap-3">
            <KPI icon={FileText} label="לקחים חדשים" value={kpiCreated} />
            <KPI icon={CheckCircle2} label="אושרו והופצו" value={kpiApproved} />
            <KPI icon={Sparkles} label="לקחים יושמו בפרויקטים עתידיים / דומים / רלוונטיים" value={kpiImplemented} />
          </div>
          <div className="rounded-lg bg-muted/30 p-3">
            <div className="text-xs text-muted-foreground mb-2">
              פירוט {kpiCreated} הלקחים החדשים לפי סטטוס:
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-1.5">
              <StatusPill icon={CheckCircle2} label="מאושרים" value={createdApproved} />
              <StatusPill icon={Clock} label="ממתינים" value={createdPending} />
              <StatusPill icon={PenLine} label="טיוטות" value={createdDraft} />
            </div>
          </div>
          {createdDeltaPct !== null && (
            <div className="text-xs text-muted-foreground flex items-center gap-1.5">
              {createdDeltaPct >= 0 ? <TrendingUp className="w-3.5 h-3.5 text-primary" /> : <TrendingDown className="w-3.5 h-3.5" />}
              השוואה לתקופה הקודמת: {createdDeltaPct >= 0 ? "+" : ""}{Math.round(createdDeltaPct)}% לקחים חדשים (היו {createdPrev})
            </div>
          )}
        </CardContent>
      </Card>

      {/* ===== Zone 3 — תובנות עומק (AI, based on data only) ===== */}
      <Card className="border-0 shadow-sm">
        <CardContent className="p-4 space-y-3">
          <ZoneTitle icon={Lightbulb}>תובנות עומק</ZoneTitle>
          {insights.length > 0 ? (
            <ul className="space-y-2">
              {insights.map((i) => (
                <li key={i.key} className="rounded-lg border p-3 space-y-1.5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="text-sm font-medium">{i.sentence}</div>
                    <Badge variant="outline" className="shrink-0 text-[11px]">
                      מבוסס על {i.count} לקחים
                    </Badge>
                  </div>
                  {i.impact && (
                    <div className="text-xs text-muted-foreground">השפעה בפועל: {i.impact}</div>
                  )}
                  {i.example && (
                    <div className="text-xs text-muted-foreground">לדוגמה: {i.example}</div>
                  )}
                  <div className="text-xs text-muted-foreground">👈 {i.recommendation}</div>
                </li>

              ))}
            </ul>
          ) : (
            <div className="text-sm text-muted-foreground">
              עדיין אין מספיק נתונים לזיהוי דפוסים חוזרים בתקופה זו.
            </div>
          )}

          <div className="rounded-lg bg-primary/5 border border-primary/20 p-3 text-sm font-medium text-primary">
            {INSIGHTS_CTA}
          </div>



          {featuredLesson && (
            <div className="rounded-lg bg-accent/5 p-3 mt-3">
              <div className="flex items-center gap-2 mb-1">
                <Star className="w-4 h-4 text-accent" />
                <div className="text-sm font-semibold">לקח התקופה</div>
              </div>
              <div className="text-sm font-semibold mb-1">{featuredLesson.title}</div>
              <div className="text-xs text-muted-foreground">
                {featuredLesson.projectName && <span>פרויקט: {featuredLesson.projectName} · </span>}
                {featuredLesson.domain && <span>תחום: {featuredLesson.domain} · </span>}
                <span>מבוסס על {featuredLesson.implementedCount} יישומים</span>
              </div>
            </div>
          )}

          <div className="flex items-center justify-between rounded-lg bg-muted/30 p-3 mt-2">
            <div>
              <div className="text-xs text-muted-foreground">מדד יישום בתקופה</div>
              <div className="text-xl font-bold">
                {implementationRate !== null ? `${Math.round(implementationRate * 100)}%` : "—"}
              </div>
            </div>
            {implementationRateDelta !== null && (
              <div className={`flex items-center gap-1 text-sm ${
                implementationRateDelta >= 0 ? "text-primary" : "text-muted-foreground"
              }`}>
                {implementationRateDelta >= 0 ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
                {implementationRateDelta >= 0 ? "+" : ""}{Math.round(implementationRateDelta)} נק' אחוז מהתקופה הקודמת
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* ===== Zone 4 — מה אפשר לעשות עכשיו ===== */}
      <Card className="border-0 shadow-sm bg-primary/5">
        <CardContent className="p-4 space-y-3">
          <ZoneTitle icon={ClipboardCheck}>מה אפשר לעשות עכשיו</ZoneTitle>
          <ul className="space-y-2 text-sm">
            <li className="flex items-center gap-2">
              <ArrowLeftCircle className="w-4 h-4 text-primary shrink-0" />
              הוסיפו לקחים חדשים מהפרויקט שלכם.
            </li>
            <li className="flex items-center gap-2">
              <Briefcase className="w-4 h-4 text-primary shrink-0" />
              בדקו לקחים רלוונטיים לפרויקטים שאתם מנהלים.
            </li>
            <li className="flex items-center gap-2">
              <UserCheck className="w-4 h-4 text-primary shrink-0" />
              תנו מענה ללקחים בתחום האחריות שלכם (רפרנטים).
            </li>
          </ul>
          <div className="border-t pt-2">
            <div className="text-sm leading-relaxed">{managerMsg}</div>
          </div>
        </CardContent>
      </Card>

      <div className="text-center text-[10px] text-muted-foreground pt-2">
        עלון זה הופק אוטומטית ממערכת הלקחים · להפצה פנים-ארגונית
      </div>
    </div>
  );
});

NewsletterPreview.displayName = "NewsletterPreview";
export default NewsletterPreview;
