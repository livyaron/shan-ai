import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Badge } from "@/components/ui/badge";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { TrendingUp, Repeat, Target, UserCog, FolderKanban } from "lucide-react";
import { useUser, type Lesson, type LessonImplementation, type Project } from "@/context/UserContext";
import {
  ADMIN_THRESHOLDS,
  computeMonthlyTrends,
  computeRepeating,
  computeByReferent,
  computeByProjectManager,
  computeDomainQuality,
  type RepeatingDimension,
} from "@/lib/adminAnalytics";
import { buildLessonDrilldownPath } from "@/lib/drilldown";
import AdminInsightsPanel from "./AdminInsightsPanel";

interface Props {
  lessons: Lesson[];
  projects: Project[];
  implementations: LessonImplementation[];
}

const SERIES = {
  created:     { color: "hsl(220, 65%, 28%)", label: "נפתחו" },
  approved:    { color: "hsl(152, 60%, 42%)", label: "אושרו והופצו" },
  implemented: { color: "hsl(205, 80%, 50%)", label: "יושמו" },
  notRelevant: { color: "hsl(0, 72%, 51%)",   label: "לא רלוונטי" },
} as const;

const AdminAnalyticsTabs = ({ lessons, projects, implementations }: Props) => {
  const navigate = useNavigate();
  const { users } = useUser();
  const [repDim, setRepDim] = useState<RepeatingDimension>("category");

  const goto = (ids: number[], tab: "repository" | "board" = "repository") => {
    if (!ids?.length) return;
    navigate(buildLessonDrilldownPath(ids, tab));
  };

  const trends = useMemo(
    () => computeMonthlyTrends(lessons, implementations, ADMIN_THRESHOLDS.MONTHS_DEFAULT),
    [lessons, implementations],
  );

  const repeating = useMemo(() => computeRepeating(lessons, repDim), [lessons, repDim]);

  const byReferent = useMemo(() => computeByReferent(lessons, users), [lessons, users]);
  const byPM = useMemo(() => computeByProjectManager(lessons, projects, users), [lessons, projects, users]);

  const domainQuality = useMemo(() => computeDomainQuality(lessons, implementations), [lessons, implementations]);
  const topImplemented = [...domainQuality].sort((a, b) => b.implPct - a.implPct).slice(0, 5);
  const lowImplemented = [...domainQuality].filter((d) => d.created >= 3).sort((a, b) => a.implPct - b.implPct).slice(0, 5);
  const highImplDomains = domainQuality.filter((d) => d.implPct >= 0.7);
  const lowImplDomains = domainQuality.filter((d) => d.created >= 3 && d.implPct < 0.3);

  return (
    <Tabs defaultValue="trends" dir="rtl" className="space-y-4">
      <TabsList>
        <TabsTrigger value="trends" className="gap-1.5"><TrendingUp className="w-3.5 h-3.5" />מגמות</TabsTrigger>
        <TabsTrigger value="repeating" className="gap-1.5"><Repeat className="w-3.5 h-3.5" />נושאים חוזרים</TabsTrigger>
        <TabsTrigger value="quality" className="gap-1.5"><Target className="w-3.5 h-3.5" />איכות ויישום</TabsTrigger>
        <TabsTrigger value="insights">תובנות AI</TabsTrigger>
      </TabsList>

      {/* ─── TRENDS ─── */}
      <TabsContent value="trends" className="space-y-4">
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base font-heading flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-accent" />
              מגמות לאורך זמן (6 חודשים אחרונים)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart
                data={trends}
                onClick={(state: any) => {
                  const p = state?.activePayload?.[0]?.payload;
                  const key = state?.activePayload?.[0]?.dataKey;
                  if (!p || !key) return;
                  const ids: number[] =
                    key === "created" ? p.createdIds :
                    key === "approved" ? p.approvedIds :
                    key === "implemented" ? p.implementedIds :
                    key === "notRelevant" ? p.notRelevantIds : [];
                  goto(ids, "repository");
                }}
                style={{ cursor: "pointer" }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="created"     name={SERIES.created.label}     stroke={SERIES.created.color}     strokeWidth={2} />
                <Line type="monotone" dataKey="approved"    name={SERIES.approved.label}    stroke={SERIES.approved.color}    strokeWidth={2} />
                <Line type="monotone" dataKey="implemented" name={SERIES.implemented.label} stroke={SERIES.implemented.color} strokeWidth={2} />
                <Line type="monotone" dataKey="notRelevant" name={SERIES.notRelevant.label} stroke={SERIES.notRelevant.color} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
            <p className="text-[11px] text-muted-foreground text-center mt-2">לחיצה על נקודה פותחת את הלקחים של אותו חודש</p>
          </CardContent>
        </Card>
      </TabsContent>

      {/* ─── REPEATING ─── */}
      <TabsContent value="repeating" className="space-y-4">
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <CardTitle className="text-base font-heading flex items-center gap-2">
                <Repeat className="w-5 h-5 text-accent" />
                נושאים חוזרים (≥ {ADMIN_THRESHOLDS.REPEATING_MIN_PROJECTS} פרויקטים שונים)
              </CardTitle>
              <ToggleGroup type="single" value={repDim} onValueChange={(v) => v && setRepDim(v as RepeatingDimension)} size="sm">
                <ToggleGroupItem value="category" className="text-xs h-7 px-2">קטגוריה</ToggleGroupItem>
                <ToggleGroupItem value="domain"   className="text-xs h-7 px-2">תחום</ToggleGroupItem>
                <ToggleGroupItem value="stage"    className="text-xs h-7 px-2">שלב</ToggleGroupItem>
              </ToggleGroup>
            </div>
          </CardHeader>
          <CardContent>
            {repeating.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">אין נושאים חוזרים</p>
            ) : (
              <div className="space-y-2">
                {repeating.slice(0, 12).map((it) => {
                  const isRepeating =
                    it.projectsCount >= ADMIN_THRESHOLDS.REPEATING_MIN_PROJECTS &&
                    it.lessonsCount >= ADMIN_THRESHOLDS.REPEATING_MIN_LESSONS;
                  return (
                    <button
                      key={it.key}
                      type="button"
                      onClick={() => goto(it.lessonIds, "repository")}
                      className="w-full text-right flex items-center justify-between gap-3 p-3 rounded-lg bg-muted/40 hover:bg-muted transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{it.key}</span>
                        {isRepeating && (
                          <Badge variant="outline" className="text-[10px] bg-accent/10 text-accent border-accent/20">
                            חוזר במספר פרויקטים
                          </Badge>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {it.projectsCount} פרויקטים · {it.lessonsCount} לקחים
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card className="border-0 shadow-sm">
            <CardHeader>
              <CardTitle className="text-base font-heading flex items-center gap-2">
                <UserCog className="w-5 h-5 text-accent" />
                עומס לפי רפרנט (בטיפול)
              </CardTitle>
            </CardHeader>
            <CardContent>
              {byReferent.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
              ) : (
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart
                    data={byReferent}
                    margin={{ top: 5, right: 10, bottom: 60, left: 0 }}
                    onClick={(s: any) => {
                      const p = s?.activePayload?.[0]?.payload;
                      if (p?.lessonIds) goto(p.lessonIds, "board");
                    }}
                    style={{ cursor: "pointer" }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
                    <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" interval={0} height={70} />
                    <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                    <Tooltip labelFormatter={(l) => byReferent.find((x) => x.name === l)?.fullName || l} />
                    <Bar dataKey="לקחים" fill="hsl(220, 65%, 28%)" radius={[6, 6, 0, 0]} barSize={26} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          <Card className="border-0 shadow-sm">
            <CardHeader>
              <CardTitle className="text-base font-heading flex items-center gap-2">
                <FolderKanban className="w-5 h-5 text-accent" />
                לפי מנהל פרויקט
              </CardTitle>
            </CardHeader>
            <CardContent>
              {byPM.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
              ) : (
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart
                    data={byPM}
                    margin={{ top: 5, right: 10, bottom: 60, left: 0 }}
                    onClick={(s: any) => {
                      const p = s?.activePayload?.[0]?.payload;
                      if (p?.lessonIds) goto(p.lessonIds, "repository");
                    }}
                    style={{ cursor: "pointer" }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
                    <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" interval={0} height={70} />
                    <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                    <Tooltip labelFormatter={(l) => byPM.find((x) => x.name === l)?.fullName || l} />
                    <Bar dataKey="לקחים" fill="hsl(32, 90%, 55%)" radius={[6, 6, 0, 0]} barSize={26} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </div>
      </TabsContent>

      {/* ─── QUALITY ─── */}
      <TabsContent value="quality" className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <button
            type="button"
            onClick={() => goto(highImplDomains.flatMap((d) => d.implementedIds), "repository")}
            className="text-right"
          >
            <Card className="border-0 shadow-sm hover:shadow-md transition-shadow cursor-pointer">
              <CardContent className="p-5">
                <p className="text-sm text-muted-foreground">תחומים עם יישום &gt; 70%</p>
                <p className="text-3xl font-bold font-heading mt-1">{highImplDomains.length}</p>
              </CardContent>
            </Card>
          </button>
          <button
            type="button"
            onClick={() => goto(lowImplDomains.flatMap((d) => d.createdIds), "repository")}
            className="text-right"
          >
            <Card className="border-0 shadow-sm hover:shadow-md transition-shadow cursor-pointer">
              <CardContent className="p-5">
                <p className="text-sm text-muted-foreground">תחומים עם יישום &lt; 30%</p>
                <p className="text-3xl font-bold font-heading mt-1 text-destructive">{lowImplDomains.length}</p>
              </CardContent>
            </Card>
          </button>
        </div>

        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base font-heading">פער בין יצירה ליישום לפי תחום מקצועי</CardTitle>
          </CardHeader>
          <CardContent>
            {domainQuality.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={domainQuality.slice(0, 10)} margin={{ top: 5, right: 10, bottom: 60, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
                  <XAxis dataKey="domain" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" interval={0} height={70} />
                  <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="created"     name="נוצרו" fill="hsl(220, 65%, 28%)" radius={[6, 6, 0, 0]}
                    onClick={(p: any) => goto(p?.createdIds, "repository")} style={{ cursor: "pointer" }} />
                  <Bar dataKey="implemented" name="יושמו" fill="hsl(152, 60%, 42%)" radius={[6, 6, 0, 0]}
                    onClick={(p: any) => goto(p?.implementedIds, "repository")} style={{ cursor: "pointer" }} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card className="border-0 shadow-sm">
            <CardHeader><CardTitle className="text-sm font-heading">Top 5 — תחומים עם יישום גבוה</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {topImplemented.length === 0 && <p className="text-sm text-muted-foreground text-center py-6">אין נתונים</p>}
              {topImplemented.map((d) => (
                <button key={d.domain} type="button" onClick={() => goto(d.implementedIds, "repository")}
                  className="w-full text-right flex items-center justify-between p-2.5 rounded-lg bg-muted/40 hover:bg-muted">
                  <span className="text-sm">{d.domain}</span>
                  <span className="text-xs text-success font-medium">{Math.round(d.implPct * 100)}%</span>
                </button>
              ))}
            </CardContent>
          </Card>
          <Card className="border-0 shadow-sm">
            <CardHeader><CardTitle className="text-sm font-heading">Top 5 — תחומים עם יישום נמוך</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {lowImplemented.length === 0 && <p className="text-sm text-muted-foreground text-center py-6">אין נתונים</p>}
              {lowImplemented.map((d) => (
                <button key={d.domain} type="button" onClick={() => goto(d.createdIds, "repository")}
                  className="w-full text-right flex items-center justify-between p-2.5 rounded-lg bg-muted/40 hover:bg-muted">
                  <span className="text-sm">{d.domain}</span>
                  <span className="text-xs text-destructive font-medium">{Math.round(d.implPct * 100)}%</span>
                </button>
              ))}
            </CardContent>
          </Card>
        </div>
      </TabsContent>

      {/* ─── AI INSIGHTS ─── */}
      <TabsContent value="insights">
        <AdminInsightsPanel />
      </TabsContent>
    </Tabs>
  );
};

export default AdminAnalyticsTabs;
