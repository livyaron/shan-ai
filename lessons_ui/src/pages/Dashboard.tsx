import { useState, useCallback, useMemo } from "react";
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
  BarChart,
  Bar,
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
import { useDashboardData } from "@/hooks/useDashboardData";
import { buildLessonDrilldownPath } from "@/lib/drilldown";
import { distinctImplementedLessonIds, distinctNotRelevantLessonIds } from "@/lib/adminAnalytics";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
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
  const pendingAdminApprovalLessonIds = visibleLessons.filter((l) => l.workflowStatus === "ממתין לאישור מנהל מערכת").map((l) => l.id);
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

  const generateAISummary = useCallback(async () => {
    if (visibleLessons.length === 0) {
      toast({ title: "אין לקחים לניתוח", variant: "destructive" });
      return;
    }

    setIsStreaming(true);
    setAiSummary("");

    try {
      const projectsData = projects.map((p) => ({
        name: p.name,
        stage: PROJECT_STAGES[p.stageIndex],
        type: projectTypeLabels[p.projectType],
        equipment: equipment.filter((eq) => p.equipmentIds.includes(eq.id)).map((eq) => eq.name),
      }));

      const userProjectsData = visibleProjects.map((p) => ({
        name: p.name,
        stage: PROJECT_STAGES[p.stageIndex],
        type: projectTypeLabels[p.projectType],
        equipment: equipment.filter((eq) => p.equipmentIds.includes(eq.id)).map((eq) => eq.name),
      }));

      const lessonsData = visibleLessons.map((l) => ({
        title: l.title,
        project: l.project,
        stage: l.stage,
        category: l.category,
        risk: l.risk,
        status: l.status,
      }));

      const CHAT_URL = `${SUPABASE_URL}/functions/v1/summarize-lessons`;

      const resp = await fetch(CHAT_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          lessons: lessonsData,
          projects: projectsData,
          equipment,
          role: currentUser.role,
          userProjects: userProjectsData,
          userName: currentUser.name,
          userId: currentUser.id,
        }),
      });

      if (!resp.ok) {
        if (resp.status === 503) {
          toast({ title: "שירות ה-AI אינו מחובר כרגע", description: "האפשרות תחזור כשיחובר מודל", variant: "destructive" });
          setIsStreaming(false);
          return;
        }
        if (resp.status === 429) {
          toast({ title: "חריגה ממגבלת בקשות", description: "נסה שוב בעוד דקה", variant: "destructive" });
          setIsStreaming(false);
          return;
        }
        if (resp.status === 402) {
          toast({ title: "נדרש תשלום", description: "הוסף קרדיטים לחשבון", variant: "destructive" });
          setIsStreaming(false);
          return;
        }
        throw new Error("Failed to start stream");
      }

      if (!resp.body) throw new Error("No response body");

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let textBuffer = "";
      let accumulated = "";
      let streamDone = false;

      while (!streamDone) {
        const { done, value } = await reader.read();
        if (done) break;
        textBuffer += decoder.decode(value, { stream: true });

        let newlineIndex: number;
        while ((newlineIndex = textBuffer.indexOf("\n")) !== -1) {
          let line = textBuffer.slice(0, newlineIndex);
          textBuffer = textBuffer.slice(newlineIndex + 1);

          if (line.endsWith("\r")) line = line.slice(0, -1);
          if (line.startsWith(":") || line.trim() === "") continue;
          if (!line.startsWith("data: ")) continue;

          const jsonStr = line.slice(6).trim();
          if (jsonStr === "[DONE]") {
            streamDone = true;
            break;
          }

          try {
            const parsed = JSON.parse(jsonStr);
            const content = parsed.choices?.[0]?.delta?.content as string | undefined;
            if (content) {
              accumulated += content;
              setAiSummary(accumulated);
            }
          } catch {
            textBuffer = line + "\n" + textBuffer;
            break;
          }
        }
      }

      // Final flush
      if (textBuffer.trim()) {
        for (let raw of textBuffer.split("\n")) {
          if (!raw) continue;
          if (raw.endsWith("\r")) raw = raw.slice(0, -1);
          if (raw.startsWith(":") || raw.trim() === "") continue;
          if (!raw.startsWith("data: ")) continue;
          const jsonStr = raw.slice(6).trim();
          if (jsonStr === "[DONE]") continue;
          try {
            const parsed = JSON.parse(jsonStr);
            const content = parsed.choices?.[0]?.delta?.content as string | undefined;
            if (content) {
              accumulated += content;
              setAiSummary(accumulated);
            }
          } catch { /* ignore */ }
        }
      }
    } catch (e) {
      console.error("AI summary error:", e);
      toast({ title: "שגיאה ביצירת סיכום AI", variant: "destructive" });
    } finally {
      setIsStreaming(false);
    }
  }, [visibleLessons, projects, equipment]);

  // Unique professional domains in the data
  const availableDomains = useMemo(() => {
    const domains = new Set<string>();
    baseLessons.forEach((l) => { if (l.professionalDomain) domains.add(l.professionalDomain); });
    return [...domains].sort();
  }, [baseLessons]);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-heading font-bold text-foreground">שלום, {currentUser.name} 👋</h1>
        <p className="text-muted-foreground mt-1">
          {roleLabels[currentUser.role]} · {currentUser.role === "admin" ? "סקירה כללית של כל המערכת" : `${projectCount} פרויקטים שהוקצו לך`}
        </p>
      </div>

      {/* Filters */}
      <Collapsible open={filtersOpen} onOpenChange={setFiltersOpen}>
        <CollapsibleTrigger asChild>
          <Button variant="outline" size="sm" className="gap-2">
            <Filter className="w-4 h-4" />
            סינון
            {hasActiveFilters && <Badge variant="default" className="text-[10px] px-1.5 py-0">{[filterProject, filterWorkflowStatus, filterCategory, filterDomain, filterSla].filter((f) => f !== "all").length}</Badge>}
            {filtersOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-3">
          <Card className="border-0 shadow-sm">
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

      <Tabs defaultValue="overview" dir="rtl">
        <TabsList className="mb-6">
          <TabsTrigger value="overview">סקירה כללית</TabsTrigger>
          <TabsTrigger value="kpi">מדדי ביצוע (KPIs)</TabsTrigger>
          {currentUser.role === "admin" && (
            <TabsTrigger value="analytics">ניתוח ניהולי</TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="overview" className="space-y-8">

      {currentUser.role === "admin" && pendingAdminApproval > 0 && (
        <div
          className="bg-accent/10 border border-accent/20 rounded-xl p-4 text-sm text-foreground cursor-pointer hover:bg-accent/15 transition-colors"
          onClick={() => navigateToLessons(pendingAdminApprovalLessonIds, "board")}
        >
          ⏳ יש {pendingAdminApproval} לקחים הממתינים לאישור שלך
        </div>
      )}

      {/* Stat cards - clickable for drill-down */}
      <div className={`grid grid-cols-1 sm:grid-cols-2 ${currentUser.role === "admin" ? "lg:grid-cols-4 xl:grid-cols-7" : "lg:grid-cols-4"} gap-4`}>
        {stats.map((stat) => (
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

      {/* AI Insights Card */}
      <Card className="border-0 shadow-sm overflow-hidden">
        <CardHeader className="bg-gradient-to-l from-accent/5 to-primary/5">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Sparkles className="w-5 h-5 text-accent" />
              תובנות AI
            </CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={generateAISummary}
              disabled={isStreaming}
              className="gap-1.5"
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
          </div>
        </CardHeader>
        <CardContent className="p-5">
          {!aiSummary && !isStreaming && (
            <div className="text-center py-8 text-muted-foreground">
              <Brain className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">לחץ על "צור ניתוח AI" לקבלת סיכום תובנות על כל הלקחים</p>
            </div>
          )}
          {isStreaming && !aiSummary && (
            <div className="flex items-center gap-3 py-6 justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-accent" />
              <p className="text-sm text-muted-foreground">AI מנתח את הלקחים...</p>
            </div>
          )}
          {aiSummary && (
            <div className="space-y-3">
              <div className="prose prose-sm max-w-none text-foreground leading-relaxed whitespace-pre-wrap">
                {aiSummary}
                {isStreaming && <span className="inline-block w-1.5 h-4 bg-accent animate-pulse mr-0.5 align-middle rounded-sm" />}
              </div>
              {!isStreaming && (
                <div className="flex justify-end">
                  <AIFeedbackDialog contextType="dashboard" />
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2 border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <TrendingUp className="w-5 h-5 text-accent" />
              לקחים לפי חודש
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart
                data={barData}
                onClick={(state: any) => {
                    const lessonIds = state?.activePayload?.[0]?.payload?.lessonIds;
                    if (lessonIds) navigateToLessons(lessonIds, "repository");
                }}
                style={{ cursor: "pointer" }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="לקחים" fill="hsl(220, 65%, 28%)" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base font-heading">לקחים לפי שלב</CardTitle>
          </CardHeader>
          <CardContent>
            {(() => {
                const stageCounts = PROJECT_STAGES.map((stage, i) => ({
                name: stage,
                value: visibleLessons.filter((l) => l.stage === stage).length,
                  lessonIds: visibleLessons.filter((l) => l.stage === stage).map((l) => l.id),
                color: STAGE_COLORS[i % STAGE_COLORS.length],
              })).filter((s) => s.value > 0);

              if (stageCounts.length === 0) {
                return <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>;
              }

              return (
                <>
                  <ResponsiveContainer width="100%" height={200}>
                    <PieChart>
                      <Pie
                        data={stageCounts}
                        cx="50%"
                        cy="50%"
                        innerRadius={50}
                        outerRadius={80}
                        dataKey="value"
                        onClick={(_: any, index: number) => {
                          const selectedLessonIds = stageCounts[index]?.lessonIds;
                          if (selectedLessonIds) navigateToLessons(selectedLessonIds, "repository");
                        }}
                        className="cursor-pointer"
                      >
                        {stageCounts.map((entry, i) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex flex-wrap gap-3 mt-2 justify-center">
                      {stageCounts.map((d) => (
                        <button key={d.name} type="button" className="flex items-center gap-1.5 text-xs hover:text-foreground transition-colors" onClick={() => navigateToLessons(d.lessonIds, "repository")}>
                        <div className="w-2.5 h-2.5 rounded-full" style={{ background: d.color }} />
                        {d.name} ({d.value})
                        </button>
                    ))}
                  </div>
                </>
              );
            })()}
          </CardContent>
        </Card>
      </div>

      {/* Equipment / Main Topics Bar Chart */}
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Wrench className="w-5 h-5 text-accent" />
            לקחים לפי ציוד / נושאים עיקריים
          </CardTitle>
        </CardHeader>
        <CardContent>
          {(() => {
            const eqCounts = equipment.map((eq) => ({
              id: eq.id,
              name: eq.name,
              לקחים: visibleLessons.filter((l) => l.equipmentIds.includes(eq.id)).length,
              lessonIds: visibleLessons.filter((l) => l.equipmentIds.includes(eq.id)).map((l) => l.id),
            })).filter((e) => e.לקחים > 0).sort((a, b) => b.לקחים - a.לקחים);

            if (eqCounts.length === 0) {
              return <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>;
            }

            return (
              <ResponsiveContainer width="100%" height={300}>
                <BarChart
                  data={eqCounts}
                  margin={{ top: 5, right: 20, bottom: 60, left: 10 }}
                  onClick={(state: any) => {
                    const lessonIds = state?.activePayload?.[0]?.payload?.lessonIds;
                    if (lessonIds) navigateToLessons(lessonIds, "repository");
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 88%)" />
                  <XAxis type="category" dataKey="name" tick={{ fontSize: 12, fontWeight: 500 }} angle={-45} textAnchor="end" interval={0} height={80} />
                  <YAxis type="number" tick={{ fontSize: 12 }} allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="לקחים" fill="hsl(32, 90%, 55%)" radius={[6, 6, 0, 0]} barSize={32} />
                </BarChart>
              </ResponsiveContainer>
            );
          })()}
        </CardContent>
      </Card>

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Clock className="w-5 h-5 text-accent" />
            לקחים אחרונים
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {visibleLessons.slice(0, 4).map((lesson) => (
              <div key={lesson.id} className="flex items-center justify-between p-4 rounded-xl bg-muted/50 hover:bg-muted transition-colors cursor-pointer" onClick={() => navigate(`/lessons/${lesson.id}`)}>
                <div className="flex-1">
                  <p className="text-sm font-medium">{lesson.title}</p>
                  <p className="text-xs text-muted-foreground mt-1">{lesson.project}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-muted-foreground">{statusLabels[lesson.status]}</span>
                  {riskBadge(lesson.risk)}
                </div>
              </div>
            ))}
            {visibleLessons.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-8">אין לקחים להצגה</p>
            )}
          </div>
        </CardContent>
      </Card>
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
