import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  CheckCircle2, Clock, RefreshCw, AlertTriangle, Target, TrendingUp, Wrench, Layers,
} from "lucide-react";
import type { Lesson, Project, LessonImplementation, Equipment, ReferentReview } from "@/context/UserContext";
import { buildLessonDrilldownPath } from "@/lib/drilldown";

interface KPIDashboardProps {
  lessons: Lesson[];
  projects: Project[];
  implementations: LessonImplementation[];
  equipment: Equipment[];
  referentReviews: ReferentReview[];
}

const IMPL_COLORS = [
  "hsl(152, 60%, 42%)",
  "hsl(205, 80%, 50%)",
  "hsl(0, 72%, 51%)",
  "hsl(220, 15%, 70%)",
];

const KPIDashboard = ({ lessons, projects, implementations, equipment, referentReviews }: KPIDashboardProps) => {
  const navigate = useNavigate();
  const navigateToLessonIds = (lessonIds: number[], tab: "repository" | "board" = "repository") => {
    navigate(buildLessonDrilldownPath(lessonIds, tab));
  };

  const kpis = useMemo(() => {
    const implementationsByLesson = new Map<number, LessonImplementation[]>();
    implementations.forEach((implementation) => {
      const existing = implementationsByLesson.get(implementation.lessonId) || [];
      implementationsByLesson.set(implementation.lessonId, [...existing, implementation]);
    });

    const implementedLessonIds: number[] = [];
    const relevantPendingLessonIds: number[] = [];
    const notRelevantLessonIds: number[] = [];
    const noResponseLessonIds: number[] = [];

    implementationsByLesson.forEach((lessonImplementations, lessonId) => {
      const hasImplemented = lessonImplementations.some((item) => item.isImplemented === true);
      const hasRelevantPending = !hasImplemented && lessonImplementations.some((item) => item.isRelevant === true);
      const allAnswered = lessonImplementations.every((item) => item.isRelevant !== undefined && item.isRelevant !== null);
      const allNotRelevant = allAnswered && lessonImplementations.every((item) => item.isRelevant === false);

      if (hasImplemented) {
        implementedLessonIds.push(lessonId);
      } else if (hasRelevantPending) {
        relevantPendingLessonIds.push(lessonId);
      } else if (allNotRelevant) {
        notRelevantLessonIds.push(lessonId);
      } else {
        noResponseLessonIds.push(lessonId);
      }
    });

    const distributedLessonIds = [...implementationsByLesson.keys()];
    const implementationRate = distributedLessonIds.length > 0
      ? Math.round((implementedLessonIds.length / distributedLessonIds.length) * 100)
      : 0;

    const implWithTime = implementedLessonIds
      .map((lessonId) => {
        const lesson = lessons.find((item) => item.id === lessonId);
        const firstImplementedResponse = (implementationsByLesson.get(lessonId) || [])
          .filter((item) => item.isImplemented && item.respondedAt)
          .sort((a, b) => new Date(a.respondedAt || "").getTime() - new Date(b.respondedAt || "").getTime())[0];

        if (!lesson?.date || !firstImplementedResponse?.respondedAt) return null;
        return { lesson, respondedAt: firstImplementedResponse.respondedAt };
      })
      .filter(Boolean) as Array<{ lesson: Lesson; respondedAt: string }>;

    let avgDays = 0;
    if (implWithTime.length > 0) {
      const totalDays = implWithTime.reduce((sum, item) => {
        const start = new Date(item.lesson.date).getTime();
        const end = new Date(item.respondedAt).getTime();
        if (isNaN(start) || isNaN(end)) return sum;
        return sum + (end - start) / (1000 * 60 * 60 * 24);
      }, 0);
      avgDays = Math.round(totalDays / implWithTime.length);
    }

    const catCounts: Record<string, number> = {};
    lessons.forEach((l) => {
      catCounts[l.category] = (catCounts[l.category] || 0) + 1;
    });
    const recurringCategories = Object.entries(catCounts)
      .filter(([, count]) => count >= 3)
      .sort((a, b) => b[1] - a[1])
      .map(([category, count]) => ({
        category,
        count,
        lessonIds: lessons.filter((lesson) => lesson.category === category).map((lesson) => lesson.id),
      }));

    const pieData = [
      { name: "יושם", value: implementedLessonIds.length, lessonIds: implementedLessonIds, color: IMPL_COLORS[0] },
      { name: "רלוונטי (טרם יושם)", value: relevantPendingLessonIds.length, lessonIds: relevantPendingLessonIds, color: IMPL_COLORS[1] },
      { name: "לא רלוונטי", value: notRelevantLessonIds.length, lessonIds: notRelevantLessonIds, color: IMPL_COLORS[2] },
      { name: "ללא תגובה", value: noResponseLessonIds.length, lessonIds: noResponseLessonIds, color: IMPL_COLORS[3] },
    ].filter((d) => d.value > 0);

    const projectComparison = projects.map((p) => {
      const projectLessonIds = lessons.filter((l) => l.projectId === p.id).map((l) => l.id);
      const projectImpls = implementations.filter((i) => i.projectId === p.id);
      const projectImplementedLessonIds = [...new Set(projectImpls.filter((i) => i.isImplemented).map((i) => i.lessonId))];
      const rate = projectLessonIds.length > 0 ? Math.round((projectImplementedLessonIds.length / projectLessonIds.length) * 100) : 0;
      return {
        id: p.id,
        name: p.name.length > 15 ? p.name.slice(0, 15) + "..." : p.name,
        fullName: p.name,
        lessonIds: projectLessonIds,
        לקחים: projectLessonIds.length,
        "אחוז יישום": rate,
        risk: p.risk,
      };
    }).sort((a, b) => b.לקחים - a.לקחים);

    const eqRiskCounts: Record<number, { name: string; high: number; total: number; lessonIds: Set<number> }> = {};
    lessons.forEach((l) => {
      l.equipmentIds.forEach((eqId) => {
        if (!eqRiskCounts[eqId]) {
          const eq = equipment.find((e) => e.id === eqId);
          eqRiskCounts[eqId] = { name: eq?.name || `ציוד ${eqId}`, high: 0, total: 0, lessonIds: new Set<number>() };
        }
        eqRiskCounts[eqId].total++;
        eqRiskCounts[eqId].lessonIds.add(l.id);
        if (l.risk === "high") eqRiskCounts[eqId].high++;
      });
    });
    const topEquipment = Object.values(eqRiskCounts)
      .map((item) => ({ ...item, lessonIds: [...item.lessonIds] }))
      .sort((a, b) => b.high - a.high || b.total - a.total)
      .slice(0, 5);

    const inTreatmentStatuses = ["בטיפול רפרנט", "בטיפול", "ממתין לאישור מנהל מערכת"];
    const inTreatmentLessonIds = lessons.filter((l) => inTreatmentStatuses.includes(l.workflowStatus)).map((l) => l.id);
    const inTreatment = inTreatmentLessonIds.length;

    const now = new Date();
    const overdueLessonIds = lessons.filter((l) => {
      if (!l.referentStartDate) return false;
      if (l.workflowStatus === "נסגר" || l.workflowStatus === "נדחה") return false;
      const start = new Date(l.referentStartDate);
      const daysDiff = Math.floor((now.getTime() - start.getTime()) / (1000 * 60 * 60 * 24));
      return daysDiff > 10;
    }).map((l) => l.id);
    const overdue = overdueLessonIds.length;

    const decisionCounts: Record<string, Set<number>> = {};
    referentReviews.forEach((r) => {
      if (r.decision) {
        if (!decisionCounts[r.decision]) {
          decisionCounts[r.decision] = new Set<number>();
        }
        decisionCounts[r.decision].add(r.lessonId);
      }
    });
    const DECISION_COLORS = [
      "hsl(152, 60%, 42%)",
      "hsl(32, 90%, 55%)",
      "hsl(205, 80%, 50%)",
      "hsl(280, 60%, 55%)",
      "hsl(220, 15%, 70%)",
      "hsl(0, 72%, 51%)",
    ];
    const decisionPieData = Object.entries(decisionCounts).map(([name, lessonIds], i) => ({
      name,
      value: lessonIds.size,
      lessonIds: [...lessonIds],
      color: DECISION_COLORS[i % DECISION_COLORS.length],
    }));

    return {
      implementationRate,
      avgDays,
      recurringCategories,
      totalDistributions: distributedLessonIds.length,
      pieData,
      projectComparison,
      topEquipment,
      implementedLessonIds,
      distributedLessonIds,
      recurringCategoryLessonIds: recurringCategories.flatMap((item) => item.lessonIds),
      inTreatment,
      inTreatmentLessonIds,
      overdue,
      overdueLessonIds,
      decisionPieData,
    };
  }, [lessons, projects, implementations, equipment, referentReviews]);

  const statCards = [
    { label: "אחוז יישום", value: `${kpis.implementationRate}%`, icon: Target, color: "bg-success", onClick: () => navigateToLessonIds(kpis.implementedLessonIds, "repository") },
    { label: "זמן ממוצע ליישום", value: kpis.avgDays > 0 ? `${kpis.avgDays} ימים` : "—", icon: Clock, color: "bg-info", onClick: () => navigateToLessonIds(kpis.implementedLessonIds, "repository") },
    { label: "לקחים בטיפול", value: kpis.inTreatment, icon: RefreshCw, color: "bg-warning", onClick: () => navigateToLessonIds(kpis.inTreatmentLessonIds, "board") },
    { label: "לקחים באיחור", value: kpis.overdue, icon: AlertTriangle, color: "bg-destructive", onClick: () => navigateToLessonIds(kpis.overdueLessonIds, "board") },
    { label: "קטגוריות חוזרות", value: kpis.recurringCategories.length, icon: RefreshCw, color: "bg-accent", onClick: () => navigateToLessonIds(kpis.recurringCategoryLessonIds, "repository") },
    { label: "סה״כ הפצות", value: kpis.totalDistributions, icon: TrendingUp, color: "bg-primary", onClick: () => navigateToLessonIds(kpis.distributedLessonIds, "repository") },
  ];

  return (
    <div className="space-y-6">
      {/* Stat cards - clickable */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
        {statCards.map((stat) => (
          <Card
            key={stat.label}
            className="border-0 shadow-sm hover:shadow-md transition-shadow cursor-pointer"
            onClick={stat.onClick}
          >
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">{stat.label}</p>
                  <p className="text-3xl font-bold font-heading mt-1">{stat.value}</p>
                </div>
                <div className={`w-12 h-12 rounded-xl ${stat.color} flex items-center justify-center`}>
                  <stat.icon className="w-6 h-6 text-primary-foreground" />
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Project comparison - clickable bars */}
        <Card className="lg:col-span-2 border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Layers className="w-5 h-5 text-accent" />
              השוואה בין פרויקטים — כמות לקחים
            </CardTitle>
          </CardHeader>
          <CardContent>
            {kpis.projectComparison.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart
                  data={kpis.projectComparison}
                  margin={{ top: 5, right: 20, bottom: 60, left: 10 }}
                  onClick={(data) => {
                    const lessonIds = data?.activePayload?.[0]?.payload?.lessonIds;
                    if (lessonIds) {
                      navigateToLessonIds(lessonIds, "repository");
                    }
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-45} textAnchor="end" interval={0} height={80} />
                  <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                  <Tooltip
                    formatter={(value: number, name: string) => [value, name === "לקחים" ? "לקחים" : "אחוז יישום"]}
                    labelFormatter={(label) => {
                      const item = kpis.projectComparison.find((p) => p.name === label);
                      return item?.fullName || label;
                    }}
                  />
                  <Bar dataKey="לקחים" fill="hsl(220, 65%, 28%)" radius={[6, 6, 0, 0]} barSize={28} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        {/* Implementation status pie */}
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <CheckCircle2 className="w-5 h-5 text-accent" />
              התפלגות סטטוס יישום
            </CardTitle>
          </CardHeader>
          <CardContent>
            {kpis.pieData.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={200}>
                  <PieChart>
                    <Pie
                      data={kpis.pieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={50}
                      outerRadius={80}
                      dataKey="value"
                      onClick={(_, index) => {
                        const lessonIds = kpis.pieData[index]?.lessonIds;
                        if (lessonIds) {
                          navigateToLessonIds(lessonIds, "repository");
                        }
                      }}
                      className="cursor-pointer"
                    >
                      {kpis.pieData.map((entry, i) => (
                        <Cell key={i} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex flex-wrap gap-3 mt-2 justify-center">
                  {kpis.pieData.map((d) => (
                    <button key={d.name} type="button" className="flex items-center gap-1.5 text-xs hover:text-foreground transition-colors" onClick={() => navigateToLessonIds(d.lessonIds, "repository")}>
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top problematic equipment */}
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Wrench className="w-5 h-5 text-accent" />
              Top 5 ציוד בעייתי
            </CardTitle>
          </CardHeader>
          <CardContent>
            {kpis.topEquipment.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>ציוד</TableHead>
                    <TableHead>לקחים בסיכון גבוה</TableHead>
                    <TableHead>סה״כ לקחים</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {kpis.topEquipment.map((eq) => {
                    const equipmentId = equipment.find((item) => item.name === eq.name)?.id;
                    return (
                    <TableRow
                      key={eq.name}
                      className={equipmentId ? "cursor-pointer hover:bg-muted/50" : undefined}
                      onClick={() => equipmentId && navigateToLessonIds(eq.lessonIds, "repository")}
                    >
                      <TableCell className="font-medium">{eq.name}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="bg-destructive/10 text-destructive border-destructive/20">
                          {eq.high}
                        </Badge>
                      </TableCell>
                      <TableCell>{eq.total}</TableCell>
                    </TableRow>
                  )})}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* Recurring categories - clickable */}
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <AlertTriangle className="w-5 h-5 text-accent" />
              קטגוריות חוזרות (3+ לקחים)
            </CardTitle>
          </CardHeader>
          <CardContent>
            {kpis.recurringCategories.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">אין קטגוריות חוזרות</p>
            ) : (
              <div className="space-y-3">
                {kpis.recurringCategories.map(({ category, count, lessonIds }) => (
                  <div
                    key={category}
                    className="flex items-center justify-between p-3 rounded-xl bg-muted/50 cursor-pointer hover:bg-muted transition-colors"
                    onClick={() => navigateToLessonIds(lessonIds, "repository")}
                  >
                    <span className="text-sm font-medium">{category}</span>
                    <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20">
                      {count} לקחים
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Referent Decision Distribution Chart */}
      {kpis.decisionPieData.length > 0 && (
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <CheckCircle2 className="w-5 h-5 text-accent" />
              התפלגות החלטות רפרנט
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={kpis.decisionPieData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} dataKey="value" onClick={(_, index) => {
                  const lessonIds = kpis.decisionPieData[index]?.lessonIds;
                  if (lessonIds) {
                    navigateToLessonIds(lessonIds, "repository");
                  }
                }} className="cursor-pointer">
                  {kpis.decisionPieData.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
            <div className="flex flex-wrap gap-3 mt-2 justify-center">
              {kpis.decisionPieData.map((d) => (
                <button key={d.name} type="button" className="flex items-center gap-1.5 text-xs hover:text-foreground transition-colors" onClick={() => navigateToLessonIds(d.lessonIds, "repository")}>
                  <div className="w-2.5 h-2.5 rounded-full" style={{ background: d.color }} />
                  {d.name} ({d.value})
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default KPIDashboard;
