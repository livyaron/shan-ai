import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  CheckCircle2, Clock, RefreshCw, AlertTriangle, Target, Layers, Hourglass, XCircle, FolderKanban, Wrench,
} from "lucide-react";
import type { Lesson, Project, LessonImplementation, ReferentReview, MockUser } from "@/context/UserContext";
import { buildLessonDrilldownPath } from "@/lib/drilldown";
import { ACTIVE_WORKFLOW_STATUSES } from "@/lib/constants";

interface Props {
  currentUser: MockUser;
  lessons: Lesson[];
  projects: Project[];
  implementations: LessonImplementation[];
  referentReviews: ReferentReview[];
  myProjectIds: number[];
}

const COLORS = [
  "hsl(220, 65%, 28%)",
  "hsl(32, 90%, 55%)",
  "hsl(152, 60%, 42%)",
  "hsl(205, 80%, 50%)",
  "hsl(280, 60%, 55%)",
  "hsl(0, 72%, 51%)",
  "hsl(45, 85%, 50%)",
  "hsl(170, 50%, 45%)",
];

// Uses canonical workflow status strings from src/lib/constants.ts
const PM_IN_TREATMENT_STATUSES = ["חדש", "בטיפול רפרנט", "ממתין לאישור מנהל מערכת"];

const RoleKPIDashboard = ({ currentUser, lessons, projects, implementations, referentReviews, myProjectIds }: Props) => {
  const navigate = useNavigate();
  const goto = (ids: number[], tab: "repository" | "board" = "repository") => {
    if (!ids || ids.length === 0) return;
    navigate(buildLessonDrilldownPath(ids, tab));
  };

  const [adminToggle, setAdminToggle] = useState<"category" | "domain">("category");

  /* ============ PROJECT MANAGER ============ */
  const pm = useMemo(() => {
    if (currentUser.role !== "project_manager") return null;

    const inTreatmentIds = lessons.filter((l) => PM_IN_TREATMENT_STATUSES.includes(l.workflowStatus)).map((l) => l.id);
    const approvedIds = lessons.filter((l) => l.workflowStatus === "אושר והופץ").map((l) => l.id);
    const rejectedIds = lessons.filter((l) => l.workflowStatus === "נדחה").map((l) => l.id);
    const returnedIds = lessons.filter((l) => l.workflowStatus === "ממתין להשלמת יוצר" && l.createdBy === currentUser.id).map((l) => l.id);
    const highRiskIds = lessons.filter((l) => l.risk === "high").map((l) => l.id);

    // Implementation rate across PM's projects
    const implByLesson = new Map<number, LessonImplementation[]>();
    implementations.forEach((i) => {
      const arr = implByLesson.get(i.lessonId) || [];
      implByLesson.set(i.lessonId, [...arr, i]);
    });
    const implementedIds: number[] = [];
    implByLesson.forEach((arr, lid) => {
      if (arr.some((x) => x.isImplemented === true)) implementedIds.push(lid);
    });
    const distributedCount = implByLesson.size;
    const implementationRate = distributedCount > 0
      ? Math.round((implementedIds.length / distributedCount) * 100) : 0;

    // Lessons per project (PM's own projects)
    const perProject = projects.map((p) => {
      const ids = lessons.filter((l) => l.projectId === p.id).map((l) => l.id);
      return {
        id: p.id,
        name: p.name.length > 18 ? p.name.slice(0, 18) + "…" : p.name,
        fullName: p.name,
        לקחים: ids.length,
        lessonIds: ids,
      };
    }).filter((p) => p.לקחים > 0).sort((a, b) => b.לקחים - a.לקחים);

    // Categories on my projects
    const catCounts: Record<string, number[]> = {};
    lessons.forEach((l) => {
      if (!catCounts[l.category]) catCounts[l.category] = [];
      catCounts[l.category].push(l.id);
    });
    const categoryData = Object.entries(catCounts)
      .map(([name, ids], i) => ({ name, value: ids.length, lessonIds: ids, color: COLORS[i % COLORS.length] }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);

    return {
      inTreatmentIds, approvedIds, rejectedIds, returnedIds, highRiskIds,
      implementationRate, implementedIds, distributedCount,
      perProject, categoryData,
    };
  }, [currentUser, lessons, projects, implementations]);

  /* ============ REFERENT ============ */
  const ref = useMemo(() => {
    if (currentUser.role !== "referent") return null;

    const treatedIds = [...new Set(
      referentReviews.filter((r) => r.decision != null && r.decision !== "").map((r) => r.lessonId),
    )];
    // lessons is already pre-filtered to the referent's scope by useDashboardData
    const inTreatmentIds = lessons.filter((l) => l.workflowStatus === "בטיפול רפרנט").map((l) => l.id);
    const overdueIds = lessons.filter((l) => {
      if (l.workflowStatus !== "בטיפול רפרנט") return false;
      if (!l.referentStartDate) return false;
      const days = Math.floor((Date.now() - new Date(l.referentStartDate).getTime()) / 86400000);
      return days > 10;
    }).map((l) => l.id);

    // Decision distribution (own reviews only)
    const decCounts: Record<string, number[]> = {};
    referentReviews.forEach((r) => {
      if (!r.decision) return;
      if (!decCounts[r.decision]) decCounts[r.decision] = [];
      decCounts[r.decision].push(r.lessonId);
    });
    const decisionData = Object.entries(decCounts).map(([name, ids], i) => ({
      name, value: ids.length, lessonIds: [...new Set(ids)], color: COLORS[i % COLORS.length],
    }));

    // Per-project distribution (only projects linked to referent's lessons)
    const perProject = projects.map((p) => {
      const ids = lessons.filter((l) => l.projectId === p.id).map((l) => l.id);
      return {
        id: p.id,
        name: p.name.length > 18 ? p.name.slice(0, 18) + "…" : p.name,
        fullName: p.name,
        לקחים: ids.length,
        lessonIds: ids,
      };
    }).filter((p) => p.לקחים > 0).sort((a, b) => b.לקחים - a.לקחים);

    // Avg response time (days between referentStartDate and respondedAt)
    const times: number[] = [];
    referentReviews.forEach((r) => {
      if (!r.respondedAt) return;
      const lesson = lessons.find((l) => l.id === r.lessonId);
      if (!lesson?.referentStartDate) return;
      const d = (new Date(r.respondedAt).getTime() - new Date(lesson.referentStartDate).getTime()) / 86400000;
      if (!isNaN(d) && d >= 0) times.push(d);
    });
    const avgDays = times.length > 0 ? Math.round(times.reduce((a, b) => a + b, 0) / times.length) : 0;

    return {
      inTreatmentIds, overdueIds, treatedIds, avgDays, decisionData, perProject,
    };
  }, [currentUser, lessons, projects, referentReviews]);

  /* ============ ADMIN ============ */
  const adm = useMemo(() => {
    if (currentUser.role !== "admin") return null;

    const inTreatmentIds = lessons.filter((l) => ACTIVE_WORKFLOW_STATUSES.includes(l.workflowStatus)).map((l) => l.id);
    const pendingApprovalIds = lessons.filter((l) => l.workflowStatus === "ממתין לאישור מנהל מערכת").map((l) => l.id);
    const overdueIds = lessons.filter((l) => {
      if (!l.referentStartDate) return false;
      if (["אושר והופץ", "נדחה"].includes(l.workflowStatus)) return false;
      const days = Math.floor((Date.now() - new Date(l.referentStartDate).getTime()) / 86400000);
      return days > 10;
    }).map((l) => l.id);
    const approvedIds = lessons.filter((l) => l.workflowStatus === "אושר והופץ").map((l) => l.id);
    const rejectedIds = lessons.filter((l) => l.workflowStatus === "נדחה").map((l) => l.id);

    // Per-project comparison
    const perProject = projects.map((p) => {
      const ids = lessons.filter((l) => l.projectId === p.id).map((l) => l.id);
      return {
        id: p.id,
        name: p.name.length > 15 ? p.name.slice(0, 15) + "…" : p.name,
        fullName: p.name,
        לקחים: ids.length,
        lessonIds: ids,
      };
    }).filter((p) => p.לקחים > 0).sort((a, b) => b.לקחים - a.לקחים).slice(0, 12);

    // Toggle: category vs professional domain
    const groupKey = adminToggle === "category" ? "category" : "professionalDomain";
    const groupCounts: Record<string, number[]> = {};
    lessons.forEach((l) => {
      const key = (groupKey === "category" ? l.category : l.professionalDomain) || "(לא צוין)";
      if (!groupCounts[key]) groupCounts[key] = [];
      groupCounts[key].push(l.id);
    });
    const groupData = Object.entries(groupCounts)
      .map(([name, ids], i) => ({ name, value: ids.length, lessonIds: ids, color: COLORS[i % COLORS.length] }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 10);

    return { inTreatmentIds, pendingApprovalIds, overdueIds, approvedIds, rejectedIds, perProject, groupData };
  }, [currentUser, lessons, projects, adminToggle]);

  /* ============ RENDER ============ */
  const StatCard = ({ label, value, icon: Icon, color, onClick }: { label: string; value: string | number; icon: any; color: string; onClick: () => void; }) => (
    <Card className="border-0 shadow-sm hover:shadow-md transition-shadow cursor-pointer" onClick={onClick}>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="text-3xl font-bold font-heading mt-1">{value}</p>
          </div>
          <div className={`w-12 h-12 rounded-xl ${color} flex items-center justify-center`}>
            <Icon className="w-6 h-6 text-primary-foreground" />
          </div>
        </div>
      </CardContent>
    </Card>
  );

  const BarBlock = ({ title, icon: Icon, data, dataKey = "לקחים", color = "hsl(220, 65%, 28%)" }: any) => (
    <Card className="border-0 shadow-sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base font-heading">
          <Icon className="w-5 h-5 text-accent" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart
              data={data}
              margin={{ top: 5, right: 20, bottom: 60, left: 10 }}
              onClick={(state: any) => {
                const ids = state?.activePayload?.[0]?.payload?.lessonIds;
                if (ids) goto(ids, "repository");
              }}
              style={{ cursor: "pointer" }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-45} textAnchor="end" interval={0} height={80} />
              <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
              <Tooltip
                labelFormatter={(label) => {
                  const item = data.find((d: any) => d.name === label);
                  return item?.fullName || label;
                }}
              />
              <Bar dataKey={dataKey} fill={color} radius={[6, 6, 0, 0]} barSize={28} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );

  const PieBlock = ({ title, icon: Icon, data, tab = "repository" as "repository" | "board" }: any) => (
    <Card className="border-0 shadow-sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base font-heading">
          <Icon className="w-5 h-5 text-accent" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={data}
                  cx="50%" cy="50%"
                  innerRadius={50} outerRadius={80}
                  dataKey="value"
                  onClick={(_: any, index: number) => {
                    const ids = data[index]?.lessonIds;
                    if (ids) goto(ids, tab);
                  }}
                  className="cursor-pointer"
                >
                  {data.map((e: any, i: number) => <Cell key={i} fill={e.color} />)}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
            <div className="flex flex-wrap gap-3 mt-2 justify-center">
              {data.map((d: any) => (
                <button key={d.name} type="button" className="flex items-center gap-1.5 text-xs hover:text-foreground transition-colors" onClick={() => goto(d.lessonIds, tab)}>
                  <div className="w-2.5 h-2.5 rounded-full" style={{ background: d.color }} />
                  {d.name} ({d.value})
                </button>
              ))}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );

  /* ===== PM VIEW ===== */
  if (currentUser.role === "project_manager" && pm) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
          <StatCard label="לקחים בטיפול" value={pm.inTreatmentIds.length} icon={RefreshCw} color="bg-warning" onClick={() => goto(pm.inTreatmentIds, "board")} />
          <StatCard label="הוחזרו אליי להשלמה" value={pm.returnedIds.length} icon={XCircle} color="bg-accent" onClick={() => goto(pm.returnedIds, "board")} />
          <StatCard label="אושרו והופצו" value={pm.approvedIds.length} icon={CheckCircle2} color="bg-success" onClick={() => goto(pm.approvedIds, "repository")} />
          <StatCard label="נדחו" value={pm.rejectedIds.length} icon={XCircle} color="bg-destructive" onClick={() => goto(pm.rejectedIds, "repository")} />
          <StatCard label="סיכון גבוה" value={pm.highRiskIds.length} icon={AlertTriangle} color="bg-destructive" onClick={() => goto(pm.highRiskIds, "repository")} />
          <StatCard label="אחוז יישום" value={`${pm.implementationRate}%`} icon={Target} color="bg-info" onClick={() => goto(pm.implementedIds, "repository")} />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2"><BarBlock title="לקחים לפי פרויקט שלך" icon={FolderKanban} data={pm.perProject} /></div>
          <PieBlock title="קטגוריות לקחים" icon={Layers} data={pm.categoryData} />
        </div>
      </div>
    );
  }

  /* ===== REFERENT VIEW ===== */
  if (currentUser.role === "referent" && ref) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard label="בטיפולך כעת" value={ref.inTreatmentIds.length} icon={RefreshCw} color="bg-warning" onClick={() => goto(ref.inTreatmentIds, "board")} />
          <StatCard label="באיחור SLA" value={ref.overdueIds.length} icon={AlertTriangle} color="bg-destructive" onClick={() => goto(ref.overdueIds, "board")} />
          <StatCard label="טופלו על ידך" value={ref.treatedIds.length} icon={CheckCircle2} color="bg-success" onClick={() => goto(ref.treatedIds, "repository")} />
          <StatCard label="זמן תגובה ממוצע" value={ref.avgDays > 0 ? `${ref.avgDays} ימים` : "—"} icon={Clock} color="bg-info" onClick={() => goto(ref.treatedIds, "repository")} />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2"><BarBlock title="לקחים לפי פרויקט (משויכים אליך)" icon={FolderKanban} data={ref.perProject} /></div>
          <PieBlock title="התפלגות החלטות שלך" icon={CheckCircle2} data={ref.decisionData} />
        </div>
      </div>
    );
  }

  /* ===== ADMIN VIEW ===== */
  if (currentUser.role === "admin" && adm) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
          <StatCard label="לקחים בטיפול" value={adm.inTreatmentIds.length} icon={RefreshCw} color="bg-warning" onClick={() => goto(adm.inTreatmentIds, "board")} />
          <StatCard label="ממתינים לאישורך" value={adm.pendingApprovalIds.length} icon={Hourglass} color="bg-info" onClick={() => goto(adm.pendingApprovalIds, "board")} />
          <StatCard label="באיחור SLA" value={adm.overdueIds.length} icon={AlertTriangle} color="bg-destructive" onClick={() => goto(adm.overdueIds, "board")} />
          <StatCard label="אושרו והופצו" value={adm.approvedIds.length} icon={CheckCircle2} color="bg-success" onClick={() => goto(adm.approvedIds, "repository")} />
          <StatCard label="נדחו" value={adm.rejectedIds.length} icon={XCircle} color="bg-destructive" onClick={() => goto(adm.rejectedIds, "repository")} />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2"><BarBlock title="השוואה בין פרויקטים" icon={Layers} data={adm.perProject} /></div>
          <Card className="border-0 shadow-sm">
            <CardHeader>
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <CardTitle className="flex items-center gap-2 text-base font-heading">
                  <Wrench className="w-5 h-5 text-accent" />
                  התפלגות {adminToggle === "category" ? "קטגוריות" : "תחומים מקצועיים"}
                </CardTitle>
                <ToggleGroup type="single" value={adminToggle} onValueChange={(v) => v && setAdminToggle(v as any)} size="sm">
                  <ToggleGroupItem value="category" className="text-xs h-7 px-2">קטגוריה</ToggleGroupItem>
                  <ToggleGroupItem value="domain" className="text-xs h-7 px-2">תחום</ToggleGroupItem>
                </ToggleGroup>
              </div>
            </CardHeader>
            <CardContent>
              {adm.groupData.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
              ) : (
                <>
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie data={adm.groupData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} dataKey="value"
                        onClick={(_: any, index: number) => {
                          const ids = adm.groupData[index]?.lessonIds;
                          if (ids) goto(ids, "repository");
                        }} className="cursor-pointer">
                        {adm.groupData.map((e, i) => <Cell key={i} fill={e.color} />)}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex flex-wrap gap-3 mt-2 justify-center">
                    {adm.groupData.map((d) => (
                      <button key={d.name} type="button" className="flex items-center gap-1.5 text-xs hover:text-foreground transition-colors" onClick={() => goto(d.lessonIds, "repository")}>
                        <div className="w-2.5 h-2.5 rounded-full" style={{ background: d.color }} />
                        {d.name} ({d.value})
                      </button>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  return null;
};

export default RoleKPIDashboard;
