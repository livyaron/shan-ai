import { useState, useCallback, useEffect, useMemo } from "react";
import { SUPABASE_URL } from "@/integrations/supabase/client";
import { useNavigate } from "react-router-dom";
import SearchableSelect from "@/components/ui/searchable-select";
import {
  BookOpen,
  FolderKanban,
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  Clock,
  Brain,
  Loader2,
  RefreshCw,
  Sparkles,
  Filter,
  ChevronDown,
  ChevronUp,
  Send,
  XCircle,
  Hourglass,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import { useUser, roleLabels, PROJECT_STAGES, projectTypeLabels } from "@/context/UserContext";
import { Wrench } from "lucide-react";
import { toast } from "@/hooks/use-toast";
import { riskColors, riskLabelsShort, statusLabelsShort as statusLabels, ACTIVE_WORKFLOW_STATUSES, WORKFLOW_STATUSES, workflowStatusColors, calculateSlaStatus, SLA_STATUS, PROFESSIONAL_DOMAINS } from "@/lib/constants";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import RoleKPIDashboard from "@/components/RoleKPIDashboard";
import AdminAnalyticsTabs from "@/components/admin/AdminAnalyticsTabs";
import AIFeedbackDialog from "@/components/AIFeedbackDialog";
import AISummaryView from "@/components/dashboard/AISummaryView";
import RankedBars from "@/components/dashboard/RankedBars";
import AIThinking from "@/components/dashboard/AIThinking";
import { ilDateTime } from "@/lib/ilDate";
import postcardImg from "@/assets/dashboard-postcard.jpg";
import { useDashboardData } from "@/hooks/useDashboardData";
import { buildLessonDrilldownPath } from "@/lib/drilldown";
import { awaitsAdminApproval } from "@/lib/lessonActions";
import { distinctImplementedLessonIds, distinctNotRelevantLessonIds } from "@/lib/adminAnalytics";
import {
  Collapsible,
  CollapsibleContent,
} from "@/components/ui/collapsible";

const hebrewMonths = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"];

const buildBarData = (lessons: { id: number; date: string }[]) => {
  const counts: Record<string, { count: number; lessonIds: number[] }> = {};
  lessons.forEach((l) => {
    if (!l.date) return;
    const d = new Date(l.date);
    if (isNaN(d.getTime())) return;
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    if (!counts[key]) {
      counts[key] = { count: 0, lessonIds: [] };
    }
    counts[key].count += 1;
    counts[key].lessonIds.push(l.id);
  });
  const now = new Date();
  const months: { name: string; monthKey: string; lessonIds: number[]; לקחים: number }[] = [];
  for (let i = 5; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    months.push({
      name: hebrewMonths[d.getMonth()],
      monthKey: key,
      lessonIds: counts[key]?.lessonIds || [],
      לקחים: counts[key]?.count || 0,
    });
  }
  return months;
};

const STAGE_COLORS = [
  "hsl(220, 65%, 28%)",
  "hsl(32, 90%, 55%)",
  "hsl(152, 60%, 42%)",
  "hsl(205, 80%, 50%)",
  "hsl(350, 70%, 50%)",
  "hsl(280, 60%, 50%)",
  "hsl(170, 50%, 45%)",
  "hsl(45, 85%, 50%)",
  "hsl(120, 40%, 45%)",
  "hsl(0, 60%, 55%)",
];

const riskBadge = (risk: string) => {
  return <Badge variant="outline" className={riskColors[risk]}>{riskLabelsShort[risk]}</Badge>;
};

const Dashboard = () => {
  const navigate = useNavigate();
  const { currentUser, lessons, projects, equipment, implementations, referentReviews, users, lessonCategories } = useUser();

  const [aiSummary, setAiSummary] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [aiMeta, setAiMeta] = useState<{ createdAt: string | null; cached: boolean } | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  // Dashboard filters
  const [filterProject, setFilterProject] = useState("all");
  const [filterWorkflowStatus, setFilterWorkflowStatus] = useState("all");
  const [filterCategory, setFilterCategory] = useState("all");
  const [filterDomain, setFilterDomain] = useState("all");
  const [filterSla, setFilterSla] = useState("all");

  const hasActiveFilters = filterProject !== "all" || filterWorkflowStatus !== "all" || filterCategory !== "all" || filterDomain !== "all" || filterSla !== "all";

  const clearFilters = () => {
    setFilterProject("all");
    setFilterWorkflowStatus("all");
    setFilterCategory("all");
    setFilterDomain("all");
    setFilterSla("all");
  };

  // Centralized role-based filtering — single source of truth
  const dashData = useDashboardData(currentUser, lessons, projects, referentReviews, implementations);
  const visibleProjects = dashData.projects;
  const baseLessons = dashData.lessons;

  const navigateToLessons = useCallback((lessonIds: number[], tab: "repository" | "board" = "repository") => {
    navigate(buildLessonDrilldownPath(lessonIds, tab));
  }, [navigate]);

  // Apply dashboard filters
  const visibleLessons = useMemo(() => {
    let filtered = baseLessons;
    if (filterProject !== "all") filtered = filtered.filter((l) => l.projectId === Number(filterProject));
    if (filterWorkflowStatus !== "all") filtered = filtered.filter((l) => l.workflowStatus === filterWorkflowStatus);
    if (filterCategory !== "all") filtered = filtered.filter((l) => l.category === filterCategory);
    if (filterDomain !== "all") filtered = filtered.filter((l) => l.professionalDomain === filterDomain);
    if (filterSla !== "all") {
      filtered = filtered.filter((l) => {
        const sla = calculateSlaStatus(l.referentStartDate);
        return sla.status === filterSla;
      });
    }
    return filtered;
  }, [baseLessons, filterProject, filterWorkflowStatus, filterCategory, filterDomain, filterSla]);

  const totalLessonIds = visibleLessons.map((l) => l.id);
  const totalLessons = totalLessonIds.length;
  const barData = buildBarData(visibleLessons);
  const highRiskLessonIds = visibleLessons.filter((l) => l.risk === "high").map((l) => l.id);
  const highRisk = highRiskLessonIds.length;
  const approvedLessonIds = visibleLessons
    .filter((l) => l.status === "approved" || l.status === "distributed" || l.workflowStatus === "אושר והופץ")
    .map((l) => l.id);
  const approved = approvedLessonIds.length;
  const pendingApproval = visibleLessons.filter((l) => l.status === "new").length;
  const projectCount = visibleProjects.length;

  // Operational KPIs for admin
  const inTreatmentLessonIds = visibleLessons.filter((l) => ACTIVE_WORKFLOW_STATUSES.includes(l.workflowStatus)).map((l) => l.id);
  const inTreatment = inTreatmentLessonIds.length;
  const pendingAdminApprovalLessonIds = visibleLessons.filter(awaitsAdminApproval).map((l) => l.id);
  const pendingAdminApproval = pendingAdminApprovalLessonIds.length;
  const returnedToCreatorLessonIds = visibleLessons.filter((l) => l.workflowStatus === "ממתין להשלמת יוצר").map((l) => l.id);
  const returnedToCreator = returnedToCreatorLessonIds.length;
  const inReferentTreatmentLessonIds = visibleLessons.filter((l) => l.workflowStatus === "בטיפול רפרנט").map((l) => l.id);
  const inReferentTreatment = inReferentTreatmentLessonIds.length;
  const approvedAndDistributedLessonIds = visibleLessons.filter((l) => l.workflowStatus === "אושר והופץ").map((l) => l.id);
  const approvedAndDistributed = approvedAndDistributedLessonIds.length;
  const now = new Date();
  const slaOverdueLessonIds = visibleLessons.filter((l) => {
    if (!l.referentStartDate) return false;
    if (["אושר והופץ", "נדחה"].includes(l.workflowStatus)) return false;
    const start = new Date(l.referentStartDate);
    return Math.floor((now.getTime() - start.getTime()) / 86400000) > 10;
  }).map((l) => l.id);
  const slaOverdue = slaOverdueLessonIds.length;

  // Role-based stat cards
  const getStatCards = () => {
    const base = [
      {
        label: "סה\"כ לקחים", value: totalLessons, icon: BookOpen, color: "bg-primary",
        onClick: () => navigateToLessons(totalLessonIds, "repository"),
      },
    ];

    if (currentUser.role === "admin") {
      const implementedIds = distinctImplementedLessonIds(dashData.implementations)
        .filter((id) => visibleLessons.some((l) => l.id === id));
      const notRelevantIds = distinctNotRelevantLessonIds(dashData.implementations)
        .filter((id) => visibleLessons.some((l) => l.id === id));
      return [
        ...base,
        {
          label: "לקחים פעילים", value: inTreatment, icon: RefreshCw, color: "bg-warning",
          onClick: () => navigateToLessons(inTreatmentLessonIds, "board"),
        },
        {
          label: "ממתינים לאישור", value: pendingAdminApproval, icon: Hourglass, color: "bg-info",
          onClick: () => navigateToLessons(pendingAdminApprovalLessonIds, "board"),
        },
        {
          label: "באיחור SLA", value: slaOverdue, icon: AlertTriangle, color: "bg-destructive",
          onClick: () => navigateToLessons(slaOverdueLessonIds, "board"),
        },
        {
          label: "אושרו והופצו", value: approvedAndDistributed, icon: CheckCircle2, color: "bg-success",
          onClick: () => navigateToLessons(approvedAndDistributedLessonIds, "repository"),
        },
        {
          label: "יושמו בפועל", value: implementedIds.length, icon: CheckCircle2, color: "bg-info",
          onClick: () => navigateToLessons(implementedIds, "repository"),
        },
        {
          label: "סומנו לא רלוונטי", value: notRelevantIds.length, icon: XCircle, color: "bg-destructive",
          onClick: () => navigateToLessons(notRelevantIds, "repository"),
        },
      ];
    }

    if (currentUser.role === "referent") {
      return [
        ...base,
        {
          label: "בטיפולך", value: inReferentTreatment, icon: RefreshCw, color: "bg-warning",
          onClick: () => navigateToLessons(inReferentTreatmentLessonIds, "board"),
        },
        {
          label: "סיכון גבוה", value: highRisk, icon: AlertTriangle, color: "bg-destructive",
          onClick: () => navigateToLessons(highRiskLessonIds, "repository"),
        },
        {
          label: "מאושרים/הופצו", value: approved, icon: CheckCircle2, color: "bg-success",
          onClick: () => navigateToLessons(approvedLessonIds, "repository"),
        },
      ];
    }

    // Project manager
    return [
      ...base,
      {
        label: "פרויקטים", value: projectCount, icon: FolderKanban, color: "bg-info",
        onClick: () => navigate("/projects"),
      },
      {
        label: "סיכון גבוה", value: highRisk, icon: AlertTriangle, color: "bg-destructive",
          onClick: () => navigateToLessons(highRiskLessonIds, "repository"),
      },
      {
        label: "מאושרים/הופצו", value: approved, icon: CheckCircle2, color: "bg-success",
          onClick: () => navigateToLessons(approvedLessonIds, "repository"),
      },
    ];
  };

  const stats = getStatCards();

  // What the model reads. The server keys its saved analysis on exactly this, so
  // the same data never costs tokens twice — and opening the page shows the last
  // analysis for this data instead of an empty card.
  const summaryPayload = useMemo(() => {
    const describe = (p: (typeof projects)[number]) => ({
      name: p.name,
      stage: PROJECT_STAGES[p.stageIndex],
      type: projectTypeLabels[p.projectType],
      equipment: equipment.filter((eq) => p.equipmentIds.includes(eq.id)).map((eq) => eq.name),
    });
    return JSON.stringify({
      lessons: visibleLessons.map((l) => ({
        title: l.title, project: l.project, stage: l.stage, category: l.category, risk: l.risk, status: l.status,
      })),
      projects: projects.map(describe),
      role: currentUser.role,
      userProjects: visibleProjects.map(describe),
      userName: currentUser.name,
      userId: currentUser.id,
    });
  }, [visibleLessons, projects, visibleProjects, equipment, currentUser]);

  /** POST the payload; null = nothing saved (cacheOnly) or a handled error. */
  const requestSummary = useCallback(async (cacheOnly: boolean) => {
    const body = cacheOnly ? JSON.stringify({ ...JSON.parse(summaryPayload), cacheOnly: true }) : summaryPayload;
    const resp = await fetch(`${SUPABASE_URL}/functions/v1/summarize-lessons`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    if (resp.status === 204) return null;
    if (!resp.ok) {
      if (cacheOnly) return null;
      const busy = resp.status === 503 || resp.status === 429;
      toast({ title: busy ? "שירות ה-AI עמוס כרגע" : "שגיאה ביצירת סיכום AI", description: busy ? "נסה שוב בעוד דקה" : undefined, variant: "destructive" });
      return null;
    }
    // The server sends the whole answer as OpenAI-style SSE chunks + [DONE].
    let text = "";
    for (const line of (await resp.text()).split("\n")) {
      if (!line.startsWith("data: ") || line.includes("[DONE]")) continue;
      try { text += JSON.parse(line.slice(6)).choices?.[0]?.delta?.content ?? ""; } catch { /* skip */ }
    }
    return { text, createdAt: resp.headers.get("X-Summary-Created"), cached: resp.headers.get("X-Summary-Cache") === "hit" };
  }, [summaryPayload]);

  // On open, and whenever the filters change the data: the saved analysis for
  // THIS data, or nothing — never an older analysis of different data.
  useEffect(() => {
    if (visibleLessons.length === 0) return;
    let live = true;
    setAiSummary("");
    setAiMeta(null);
    requestSummary(true).then((r) => {
      if (live && r) { setAiSummary(r.text); setAiMeta({ createdAt: r.createdAt, cached: true }); }
    }).catch(() => { /* a missing cache is just an empty card */ });
    return () => { live = false; };
  }, [requestSummary, visibleLessons.length]);

  const generateAISummary = useCallback(async () => {
    if (visibleLessons.length === 0) {
      toast({ title: "אין לקחים לניתוח", variant: "destructive" });
      return;
    }
    setIsStreaming(true);
    try {
      const r = await requestSummary(false);
      if (r) {
        setAiSummary(r.text);
        setAiMeta({ createdAt: r.createdAt, cached: r.cached });
      }
    } catch (e) {
      console.error("AI summary error:", e);
      toast({ title: "שגיאה ביצירת סיכום AI", variant: "destructive" });
    } finally {
      setIsStreaming(false);
    }
  }, [visibleLessons.length, requestSummary]);

  // Unique professional domains in the data
  const availableDomains = useMemo(() => {
    const domains = new Set<string>();
    baseLessons.forEach((l) => { if (l.professionalDomain) domains.add(l.professionalDomain); });
    return [...domains].sort();
  }, [baseLessons]);

  const stageData = PROJECT_STAGES.map((stage, i) => {
    const ids = visibleLessons.filter((l) => l.stage === stage).map((l) => l.id);
    return { name: stage, value: ids.length, lessonIds: ids, color: STAGE_COLORS[i % STAGE_COLORS.length] };
  }).filter((s) => s.value > 0);
  const stageTotal = stageData.reduce((s, d) => s + d.value, 0);

  const topicItems = equipment
    .map((eq) => {
      const ids = visibleLessons.filter((l) => l.equipmentIds.includes(eq.id)).map((l) => l.id);
      return { key: eq.id, label: eq.name, value: ids.length, lessonIds: ids };
    })
    .filter((e) => e.value > 0)
    .sort((a, b) => b.value - a.value);

  const activeFilterCount = [filterProject, filterWorkflowStatus, filterCategory, filterDomain, filterSla].filter((f) => f !== "all").length;
  const todayLabel = new Date().toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="space-y-6">
      <Tabs defaultValue="overview" dir="rtl" className="space-y-6">
      {/* Hero — the postcard (user's own photo, 2026-10-17): sky above the
          horizon, the photo's sand below it; the stat cards sit on the line. */}
      <div className="relative flex flex-col overflow-hidden rounded-3xl shadow-lg text-[#16223b] lg:min-h-[324px]">
        <div className="flex items-start justify-between gap-4 bg-gradient-to-b from-[#9db3c9] to-[#c3cdd3] pt-5 pb-12 px-5 sm:px-7 lg:pl-[280px]">
          <div>
          <p className="text-xs font-medium text-[#2c3d5c]">{todayLabel}</p>
          <h1 className="mt-1 text-2xl sm:text-3xl font-heading font-bold">שלום, {currentUser.name} 👋</h1>
          <p className="mt-1 text-sm text-[#2c3d5c]">
            {roleLabels[currentUser.role]} · {currentUser.role === "admin" ? "סקירה כללית של כל המערכת" : `${projectCount} פרויקטים שהוקצו לך`}
          </p>
          </div>
          <figure className="lg:hidden m-0 w-[84px] shrink-0 rotate-[-3.5deg] rounded bg-[#fbf6ea] p-1 pb-3 shadow-[0_14px_24px_-12px_rgba(50,32,8,0.65)]">
            <img src={postcardImg} alt="" aria-hidden className="block w-full aspect-[4/5] rounded-sm object-cover object-top" />
          </figure>
        </div>
        <div className="flex-1 bg-gradient-to-b from-[#c9ae7c] to-[#b8955f] px-5 pb-4 sm:px-7 lg:pl-[280px]">
          <div className={`-mt-8 grid grid-cols-2 sm:grid-cols-4 ${currentUser.role === "admin" ? "lg:grid-cols-4 xl:grid-cols-7" : ""} gap-2`}>
            {stats.map((stat) => (
              <button
                key={stat.label}
                type="button"
                onClick={stat.onClick}
                className="group relative overflow-hidden rounded-xl bg-[#fcf7ec] px-3 py-2.5 text-right shadow-[0_10px_22px_-12px_rgba(60,40,10,0.75)] transition-transform duration-300 hover:-translate-y-1"
              >
                <span aria-hidden className={`absolute inset-y-2 right-0 w-1 rounded-l-full ${stat.color}`} />
                <p className="text-2xl font-bold font-heading tabular-nums leading-none">{stat.value}</p>
                <p className="mt-1 text-[11px] leading-tight text-[#6b5a3d]">{stat.label}</p>
              </button>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <TabsList className="h-auto flex-wrap rounded-xl bg-white/25 p-1">
              <TabsTrigger value="overview" className="rounded-lg text-[#16223b] data-[state=active]:bg-[#fcf7ec] data-[state=active]:text-[#16223b]">סקירה כללית</TabsTrigger>
              <TabsTrigger value="kpi" className="rounded-lg text-[#16223b] data-[state=active]:bg-[#fcf7ec] data-[state=active]:text-[#16223b]">מדדי ביצוע (KPIs)</TabsTrigger>
              {currentUser.role === "admin" && (
                <TabsTrigger value="analytics" className="rounded-lg text-[#16223b] data-[state=active]:bg-[#fcf7ec] data-[state=active]:text-[#16223b]">ניתוח ניהולי</TabsTrigger>
              )}
            </TabsList>
            <div className="flex items-center gap-2">
              <span className="rounded-xl bg-[#fcf7ec]/85 px-3 py-1.5 text-xs text-[#4a5872]">
                <strong className="text-base tabular-nums text-[#16223b]">{totalLessons}</strong> לקחים בתצוגה
              </span>
              <Button
                size="sm"
                onClick={() => setFiltersOpen((v) => !v)}
                className="gap-2 rounded-xl border-0 bg-[#16223b] text-[#f4e7c5] hover:bg-[#22325a]"
              >
                <Filter className="w-4 h-4" />
                סינון
                {hasActiveFilters && <Badge className="bg-accent text-accent-foreground text-[10px] px-1.5 py-0">{activeFilterCount}</Badge>}
                {filtersOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </Button>
            </div>
          </div>
        </div>
        <figure className="hidden lg:block absolute left-7 top-4 m-0 w-[220px] rotate-[-3.5deg] rounded-md bg-[#fbf6ea] p-2.5 pb-7 shadow-[0_24px_40px_-18px_rgba(50,32,8,0.65)] transition-transform duration-500 hover:rotate-0 hover:scale-[1.03]">
          <img src={postcardImg} alt="קפיצה במדבר תחת סמל האינסוף" className="block w-full aspect-[4/5] rounded-sm object-cover object-top" />
        </figure>
      </div>

      {/* Filters */}
      <Collapsible open={filtersOpen} onOpenChange={setFiltersOpen}>
        <CollapsibleContent>
          <Card className="border-0 shadow-sm rounded-2xl">
            <CardContent className="p-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">פרויקט</label>
                  <SearchableSelect value={filterProject} onValueChange={setFilterProject} className="h-9 text-xs"
                    options={[
                      { value: "all", label: "הכל" },
                      ...visibleProjects.map((p) => ({ value: String(p.id), label: p.name })),
                    ]}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">סטטוס טיפול</label>
                  <SearchableSelect value={filterWorkflowStatus} onValueChange={setFilterWorkflowStatus} className="h-9 text-xs"
                    options={[
                      { value: "all", label: "הכל" },
                      ...WORKFLOW_STATUSES.map((s) => ({ value: String(s), label: s })),
                    ]}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">קטגוריה</label>
                  <SearchableSelect value={filterCategory} onValueChange={setFilterCategory} className="h-9 text-xs"
                    options={[
                      { value: "all", label: "הכל" },
                      ...lessonCategories.map((c) => ({ value: String(c), label: c })),
                    ]}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">תחום מקצועי</label>
                  <SearchableSelect value={filterDomain} onValueChange={setFilterDomain} className="h-9 text-xs"
                    options={[
                      { value: "all", label: "הכל" },
                      ...availableDomains.map((d) => ({ value: String(d), label: d })),
                    ]}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">מצב SLA</label>
                  <SearchableSelect value={filterSla} onValueChange={setFilterSla} className="h-9 text-xs"
                    options={[
                      { value: "all", label: "הכל" },
                      { value: String(SLA_STATUS.OK), label: "תקין" },
                      { value: String(SLA_STATUS.WARNING), label: "מתקרב לחריגה" },
                      { value: String(SLA_STATUS.OVERDUE), label: "באיחור" },
                    ]}
                  />
                </div>
              </div>
              {hasActiveFilters && (
                <Button variant="ghost" size="sm" onClick={clearFilters} className="mt-2 text-xs">
                  נקה סינונים
                </Button>
              )}
            </CardContent>
          </Card>
        </CollapsibleContent>
      </Collapsible>


        <TabsContent value="overview" className="space-y-6">

      {currentUser.role === "admin" && pendingAdminApproval > 0 && (
        <button
          type="button"
          className="w-full flex items-center gap-3 rounded-2xl border border-accent/30 bg-gradient-to-l from-accent/15 to-transparent p-4 text-sm text-foreground text-right hover:from-accent/25 transition-colors"
          onClick={() => navigateToLessons(pendingAdminApprovalLessonIds, "board")}
        >
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
          </span>
          יש <strong>{pendingAdminApproval}</strong> לקחים הממתינים לאישור שלך
        </button>
      )}

      {/* AI Insights Card */}
      <Card className="border-0 shadow-sm overflow-hidden rounded-2xl">
        <CardHeader className="relative bg-gradient-to-l from-accent/10 via-primary/5 to-transparent">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <span className="inline-flex w-8 h-8 items-center justify-center rounded-xl bg-gradient-to-br from-accent to-primary shadow-sm">
                <Sparkles className="w-4 h-4 text-primary-foreground" />
              </span>
              תובנות AI
            </CardTitle>
            {/* A saved analysis of THIS data is final: asking again would spend
                tokens on the same answer. It reopens itself once the data changes. */}
            {aiSummary && aiMeta?.createdAt && !isStreaming ? (
              <span className="inline-flex items-center gap-1.5 rounded-xl bg-success/10 px-3 py-1.5 text-xs font-medium text-success">
                <CheckCircle2 className="w-3.5 h-3.5" />
                הניתוח מעודכן לנתונים
              </span>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={generateAISummary}
                disabled={isStreaming}
                className="gap-1.5 rounded-xl"
              >
                {isStreaming ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    מנתח...
                  </>
                ) : (
                  <>
                    {aiSummary ? <RefreshCw className="w-3.5 h-3.5" /> : <Brain className="w-3.5 h-3.5" />}
                    {aiSummary ? "רענן ניתוח" : "צור ניתוח AI"}
                  </>
                )}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="p-5">
          {!aiSummary && !isStreaming && (
            <div className="text-center py-8 text-muted-foreground">
              <Brain className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">לחץ על "צור ניתוח AI" לקבלת סיכום תובנות על כל הלקחים</p>
            </div>
          )}
          {isStreaming && <AIThinking lessonCount={visibleLessons.length} />}
          {aiSummary && !isStreaming && (
            <div className="space-y-3">
              <AISummaryView text={aiSummary} streaming={isStreaming} />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {aiMeta?.createdAt
                    ? <>נוצר ב-<span dir="ltr">{ilDateTime(aiMeta.createdAt)}</span> · נשמר ויתעדכן רק כשהנתונים ישתנו</>
                    : "הניתוח לא נשמר כי לא הושלם — אפשר לנסות שוב"}
                </p>
                <AIFeedbackDialog contextType="dashboard" />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2 border-0 shadow-sm rounded-2xl">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <TrendingUp className="w-5 h-5 text-accent" />
              לקחים לפי חודש
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart
                data={barData}
                margin={{ top: 10, right: 8, bottom: 0, left: 8 }}
                onClick={(state: any) => {
                    const lessonIds = state?.activePayload?.[0]?.payload?.lessonIds;
                    if (lessonIds) navigateToLessons(lessonIds, "repository");
                }}
                style={{ cursor: "pointer" }}
              >
                <defs>
                  <linearGradient id="dashMonthFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.45} />
                    <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="name" reversed tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                <YAxis orientation="right" allowDecimals={false} width={32} tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                <Tooltip
                  cursor={{ stroke: "hsl(var(--accent))", strokeWidth: 1 }}
                  contentStyle={{ borderRadius: 12, border: "1px solid hsl(var(--border))", background: "hsl(var(--popover))", color: "hsl(var(--popover-foreground))", direction: "rtl" }}
                />
                <Area type="monotone" dataKey="לקחים" stroke="hsl(var(--primary))" strokeWidth={2.5} fill="url(#dashMonthFill)"
                  dot={{ r: 3.5, fill: "hsl(var(--card))", stroke: "hsl(var(--primary))", strokeWidth: 2 }} activeDot={{ r: 6 }} />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="border-0 shadow-sm rounded-2xl">
          <CardHeader>
            <CardTitle className="text-base font-heading">לקחים לפי שלב</CardTitle>
          </CardHeader>
          <CardContent>
            {stageData.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>
            ) : (
              <>
                <div className="relative">
                  <ResponsiveContainer width="100%" height={190}>
                    <PieChart>
                      <Pie
                        data={stageData}
                        cx="50%"
                        cy="50%"
                        innerRadius={58}
                        outerRadius={82}
                        paddingAngle={2}
                        cornerRadius={4}
                        stroke="none"
                        dataKey="value"
                        onClick={(_: any, index: number) => {
                          const selectedLessonIds = stageData[index]?.lessonIds;
                          if (selectedLessonIds) navigateToLessons(selectedLessonIds, "repository");
                        }}
                        className="cursor-pointer"
                      >
                        {stageData.map((entry, i) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={{ borderRadius: 12, border: "1px solid hsl(var(--border))", background: "hsl(var(--popover))", color: "hsl(var(--popover-foreground))", direction: "rtl" }} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-2xl font-bold tabular-nums">{stageTotal}</span>
                    <span className="text-[11px] text-muted-foreground">לקחים</span>
                  </div>
                </div>
                <div className="mt-3 space-y-1">
                  {stageData.map((d) => (
                    <button key={d.name} type="button" onClick={() => navigateToLessons(d.lessonIds, "repository")}
                      className="w-full flex items-center gap-2 rounded-lg px-2 py-1 text-xs hover:bg-muted/60 transition-colors">
                      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: d.color }} />
                      <span className="flex-1 truncate text-right">{d.name}</span>
                      <span className="font-semibold tabular-nums">{d.value}</span>
                      <span className="w-9 text-left text-muted-foreground tabular-nums">{Math.round((d.value / stageTotal) * 100)}%</span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Equipment / Main Topics */}
        <Card className="lg:col-span-3 border-0 shadow-sm rounded-2xl">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Wrench className="w-5 h-5 text-accent" />
              לקחים לפי ציוד / נושאים עיקריים
            </CardTitle>
          </CardHeader>
          <CardContent>
            <RankedBars items={topicItems} color="hsl(var(--accent))" onPick={(ids) => navigateToLessons(ids, "repository")} />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2 border-0 shadow-sm rounded-2xl">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Clock className="w-5 h-5 text-accent" />
              לקחים אחרונים
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {visibleLessons.slice(0, 5).map((lesson) => (
                <button key={lesson.id} type="button" onClick={() => navigate(`/lessons/${lesson.id}`)}
                  className="w-full flex items-center gap-3 rounded-xl border border-border/50 p-3 text-right hover:border-primary/40 hover:bg-muted/40 transition-colors">
                  <span className={`h-9 w-1 shrink-0 rounded-full ${lesson.risk === "high" ? "bg-destructive" : lesson.risk === "medium" ? "bg-warning" : "bg-success"}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{lesson.title}</p>
                    <p className="text-xs text-muted-foreground mt-0.5 truncate">{lesson.project} · {statusLabels[lesson.status]}</p>
                  </div>
                  {riskBadge(lesson.risk)}
                </button>
              ))}
              {visibleLessons.length === 0 && (
                <p className="text-sm text-muted-foreground text-center py-8">אין לקחים להצגה</p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
        </TabsContent>

        <TabsContent value="kpi">
          <RoleKPIDashboard
            currentUser={currentUser}
            lessons={visibleLessons}
            projects={visibleProjects}
            implementations={dashData.implementations}
            referentReviews={dashData.referentReviews}
            myProjectIds={dashData.myProjectIds}
          />
        </TabsContent>

        {currentUser.role === "admin" && (
          <TabsContent value="analytics">
            <AdminAnalyticsTabs
              lessons={visibleLessons}
              projects={visibleProjects}
              implementations={dashData.implementations}
            />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
};

export default Dashboard;
