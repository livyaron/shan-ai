import { useMemo, useState, useEffect } from "react";
import AIFeedbackDialog from "@/components/AIFeedbackDialog";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowRight, FolderKanban, BookOpen, CheckCircle2, ChevronLeft, Brain, Loader2, MessageSquare, ThumbsUp, ThumbsDown, Wrench, Pencil, TrendingUp, TrendingDown, Lightbulb, Shield, Plus, Presentation, FileDown } from "lucide-react";
import LessonsPresentationDialog from "@/components/LessonsPresentationDialog";
import RecommendedLessonsDialog from "@/components/RecommendedLessonsDialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useUser, PROJECT_STAGES, projectTypeLabels, stationTypeLabels } from "@/context/UserContext";
import type { ProjectType, StationType, ProjectAnalysis, RelevantLessonSuggestion } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";
import { riskColors, riskLabels, statusLabelsShort as statusLabels, priorityLabels } from "@/lib/constants";
import { useActionRequired } from "@/hooks/useActionRequired";
import { Zap } from "lucide-react";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import SiteCombobox from "@/components/SiteCombobox";
import { supabase } from "@/integrations/supabase/client";
import MultiStageSelect from "@/components/MultiStageSelect";
import SearchableSelect from "@/components/ui/searchable-select";

const ProjectDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const {
    projects, updateProject, updateProjectStage, updateProjectStages, updateProjectEquipment, equipment, lessons, currentUser, hasPermission,
    implementations, analyzeRelevantLessons, fetchRelevantLessonSuggestions, addSuggestedLessonsToProject, respondToImplementation, isAnalyzing, users,
  } = useUser();

  const [feedbackNotes, setFeedbackNotes] = useState<Record<number, string>>({});
  const [editingEquipment, setEditingEquipment] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editRisk, setEditRisk] = useState<"high" | "medium" | "low">("low");
  const [editProjectType, setEditProjectType] = useState<ProjectType>("new_build");
  const [editStationType, setEditStationType] = useState<StationType>("closed");
  const [editManagerId, setEditManagerId] = useState("");
  const [editEquipmentIds, setEditEquipmentIds] = useState<number[]>([]);
  const [editSite, setEditSite] = useState("");
  const [editStageIndexes, setEditStageIndexes] = useState<number[]>([0]);
  const [projectAnalysis, setProjectAnalysis] = useState<ProjectAnalysis | null>(null);
  const [stageConfirmOpen, setStageConfirmOpen] = useState(false);
  const [suggestedLessons, setSuggestedLessons] = useState<RelevantLessonSuggestion[]>([]);
  const [selectedSuggestionIds, setSelectedSuggestionIds] = useState<number[]>([]);
  const [isApplyingSuggestions, setIsApplyingSuggestions] = useState(false);
  const [presentationOpen, setPresentationOpen] = useState(false);
  const [recommendedOpen, setRecommendedOpen] = useState(false);

  // Load site field config
  const [siteFieldConfig, setSiteFieldConfig] = useState<{ isVisible: boolean; isRequired: boolean; displayName: string } | null>(null);
  useEffect(() => {
    const loadConfig = async () => {
      const { data } = await supabase
        .from("form_field_configs")
        .select("is_visible, is_required, display_name")
        .eq("form_type", "project")
        .eq("field_key", "site")
        .limit(1);
      if (data && data.length > 0) {
        setSiteFieldConfig({ isVisible: data[0].is_visible, isRequired: data[0].is_required, displayName: data[0].display_name });
      }
    };
    loadConfig();
  }, []);

  const actionableLessonIds = useMemo(() => {
    return new Set(
      lessons
        .filter((l) => l.status === "approved" || l.status === "distributed")
        .map((l) => l.id)
    );
  }, [lessons]);

  const { projectNeedsAction } = useActionRequired(lessons, implementations, projects, currentUser);

  const rawProject = projects.find((p) => p.id === Number(id));
  const project = currentUser.role === "admin" || currentUser.role === "viewer" || rawProject?.managerId === currentUser.id ? rawProject : undefined;
  if (!project) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">הפרויקט לא נמצא</p>
        <Button variant="ghost" onClick={() => navigate("/projects")} className="mt-4 gap-2">
          <ArrowRight className="w-4 h-4" />
          חזרה לפרויקטים
        </Button>
      </div>
    );
  }

  const canChangeStage = hasPermission("change_stage") &&
    (currentUser.role === "admin" || project.managerId === currentUser.id);

  const currentStage = PROJECT_STAGES[project.stageIndex];
  const projectLessons = lessons.filter((l) => l.projectId === project.id);
  const projectImpls = implementations.filter((impl) => impl.projectId === project.id);
  const pendingImpls = projectImpls.filter((impl) => !impl.respondedBy && actionableLessonIds.has(impl.lessonId));
  const answeredImpls = projectImpls.filter((impl) => impl.respondedBy && actionableLessonIds.has(impl.lessonId));
  const projectEquipment = equipment.filter((e) => project.equipmentIds.includes(e.id));

  const toggleEquipment = (eqId: number) => {
    const newIds = project.equipmentIds.includes(eqId)
      ? project.equipmentIds.filter((id) => id !== eqId)
      : [...project.equipmentIds, eqId];
    updateProjectEquipment(project.id, newIds);
  };

  const toggleEditEquipment = (eqId: number) => {
    setEditEquipmentIds((prev) =>
      prev.includes(eqId) ? prev.filter((id) => id !== eqId) : [...prev, eqId]
    );
  };

  const handleStageChange = async (newIndex: number) => {
    if (!canChangeStage || newIndex === project.stageIndex) return;

    try {
      await updateProjectStage(project.id, newIndex);
      const { projectAnalysis: analysis, suggestions } = await fetchRelevantLessonSuggestions(project.id, newIndex);
      if (analysis) {
        setProjectAnalysis(analysis);
      }

      if (suggestions.length === 0) {
        toast({
          title: "שלב הפרויקט עודכן ✓",
          description: `${project.name} עבר לשלב: ${PROJECT_STAGES[newIndex]} — לא נמצאו לקחים חדשים להוספה.`,
        });
        return;
      }

      setSuggestedLessons(suggestions);
      setSelectedSuggestionIds(suggestions.map((item) => item.lessonId));
      setStageConfirmOpen(true);
      toast({
        title: "שלב הפרויקט עודכן ✓",
        description: `${project.name} עבר לשלב: ${PROJECT_STAGES[newIndex]} — בחר אילו לקחים להוסיף.`,
      });
    } catch (error) {
      console.error("Stage change failed:", error);
      toast({
        title: "שגיאה בעדכון שלב",
        description: "אירעה שגיאה בעדכון השלב או בקבלת המלצות AI.",
        variant: "destructive",
      });
    }
  };

  const handleConfirmSuggestedLessons = async () => {
    const selectedSuggestions = suggestedLessons.filter((item) => selectedSuggestionIds.includes(item.lessonId));

    try {
      setIsApplyingSuggestions(true);
      const addedCount = await addSuggestedLessonsToProject(project.id, selectedSuggestions);
      setStageConfirmOpen(false);
      setSuggestedLessons([]);
      setSelectedSuggestionIds([]);

      toast({
        title: "הבחירה נשמרה ✓",
        description: addedCount > 0
          ? `${addedCount} לקחים נוספו לפרויקט.`
          : "לא נוספו לקחים לפרויקט.",
      });
    } catch (error) {
      console.error("Failed to add selected lessons:", error);
      toast({
        title: "שגיאה בהוספת לקחים",
        description: "לא הצלחנו להוסיף את הלקחים שנבחרו.",
        variant: "destructive",
      });
    } finally {
      setIsApplyingSuggestions(false);
    }
  };

  const handleCancelSuggestedLessons = (open: boolean) => {
    setStageConfirmOpen(open);
    if (!open) {
      setSuggestedLessons([]);
      setSelectedSuggestionIds([]);
    }
  };

  const handleAnalyze = async () => {
    const analysis = await analyzeRelevantLessons(project.id);
    if (analysis) {
      setProjectAnalysis(analysis);
    }
    toast({ title: "ניתוח AI הושלם ✓", description: "ניתוח הפרויקט והמלצות לקחים הושלמו" });
  };

  const handleRespond = (implId: number, isRelevant: boolean, isImplemented: boolean) => {
    respondToImplementation(implId, {
      isRelevant,
      isImplemented,
      notes: feedbackNotes[implId] || "",
    });
    toast({ title: "תגובה נשמרה ✓", description: "הפידבק שלך יעזור לשפר המלצות AI בעתיד" });
  };

  const pm = users.find((u) => u.id === project.managerId);

  const getLessonTitle = (lessonId: number) => lessons.find((l) => l.id === lessonId)?.title || `לקח #${lessonId}`;

  const openEditDialog = () => {
    setEditName(project.name);
    setEditRisk(project.risk as "high" | "medium" | "low");
    setEditProjectType(project.projectType);
    setEditStationType(project.stationType);
    setEditManagerId(project.managerId);
    setEditEquipmentIds(project.equipmentIds);
    setEditSite(project.site || "");
    setEditStageIndexes(project.stageIndexes && project.stageIndexes.length > 0 ? project.stageIndexes : [project.stageIndex]);
    setEditDialogOpen(true);
  };

  const saveProject = async () => {
    if (editStageIndexes.length === 0) {
      toast({ title: "שגיאה", description: "חובה לבחור לפחות שלב אחד", variant: "destructive" });
      return;
    }
    updateProject(project.id, {
      name: editName.trim(),
      risk: editRisk,
      projectType: editProjectType,
      stationType: editStationType,
      managerId: editManagerId,
      site: editSite || undefined,
    });
    updateProjectEquipment(project.id, editEquipmentIds);
    // Update stages if changed
    const currentSorted = [...(project.stageIndexes || [project.stageIndex])].sort((a, b) => a - b).join(",");
    const newSorted = [...editStageIndexes].sort((a, b) => a - b).join(",");
    if (currentSorted !== newSorted) {
      try {
        await updateProjectStages(project.id, editStageIndexes);
      } catch (e) {
        console.error("Stage update failed:", e);
      }
    }
    setEditDialogOpen(false);
    toast({ title: "פרטי הפרויקט עודכנו ✓" });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button variant="ghost" size="icon" onClick={() => navigate("/projects")}>
          <ArrowRight className="w-5 h-5" />
        </Button>
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <div className="w-12 h-12 rounded-xl bg-primary flex items-center justify-center shrink-0">
            <FolderKanban className="w-6 h-6 text-primary-foreground" />
          </div>
          <div className="min-w-0">
            <h1 className="text-2xl font-heading font-bold truncate">{project.name}</h1>
            <div className="flex items-center gap-2 mt-1 flex-wrap text-sm text-muted-foreground">
              <div className="flex items-center gap-1 flex-wrap">
                {(project.stageIndexes && project.stageIndexes.length > 0 ? project.stageIndexes : [project.stageIndex]).map((si) => (
                  <Badge key={si} variant="secondary" className="bg-accent/10 text-accent border-accent/20">
                    {PROJECT_STAGES[si]}
                  </Badge>
                ))}
              </div>
              <span>·</span>
              <span>{projectTypeLabels[project.projectType]}</span>
              <span>·</span>
              <span>{stationTypeLabels[project.stationType]}</span>
              {pm && <><span>·</span><span>מנהל: {pm.name}</span></>}
              {project.site && <><span>·</span><span>אתר: {project.site}</span></>}
              <Badge variant="outline" className={riskColors[project.risk]}>{riskLabels[project.risk]}</Badge>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 justify-end shrink-0">
          {canChangeStage && (
            <Button variant="outline" size="sm" onClick={openEditDialog} className="gap-1.5">
              <Pencil className="w-4 h-4" />
              עריכה
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPresentationOpen(true)}
            className="gap-1.5"
            disabled={projectLessons.length === 0}
          >
            <Presentation className="w-4 h-4" />
            מצגת לקחים
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setRecommendedOpen(true)}
            className="gap-1.5"
          >
            <FileDown className="w-4 h-4" />
            הפק מסמך לקחים מומלצים
          </Button>
          <Button
            onClick={handleAnalyze}
            disabled={isAnalyzing}
            size="sm"
            className="bg-accent text-accent-foreground hover:bg-accent/90 gap-1.5"
          >
            {isAnalyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Brain className="w-4 h-4" />}
            {isAnalyzing ? "מנתח..." : "ניתוח AI"}
          </Button>
        </div>
      </div>

      {/* Action Required Banner */}
      {projectNeedsAction(project.id).needed && (
        <Alert className="border-warning/30 bg-warning/5">
          <Zap className="w-4 h-4 text-warning" />
          <AlertTitle className="text-warning font-semibold">דורש פעולה</AlertTitle>
          <AlertDescription className="text-sm text-muted-foreground">
            {projectNeedsAction(project.id).label}
            {currentUser.role === "project_manager" && " — גלול לחלק ׳לקחים ממתינים לתגובה׳ כדי להגיב."}
            {currentUser.role === "admin" && " — עבור לדף הלקחים כדי לאשר."}
          </AlertDescription>
        </Alert>
      )}

      {/* Edit Project Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent dir="rtl" className="max-w-md max-h-[85vh] overflow-y-auto">
          <DialogHeader>
             <DialogTitle>עריכת פרטי פרויקט</DialogTitle>
             <DialogDescription>עדכן שם, סוג, סיכון ופרטים נוספים</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם הפרויקט</Label>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label>סוג פרויקט</Label>
              <SearchableSelect value={editProjectType} onValueChange={(v) => setEditProjectType(v as ProjectType)} className="mt-1.5"
                options={[
                  { value: "new_build", label: "הקמה" },
                  { value: "expansion", label: "הרחבה" },
                  { value: "maintenance", label: "שו\"ש" },
                ]}
              />
            </div>
            <div>
              <Label>סוג תחנה</Label>
              <SearchableSelect value={editStationType} onValueChange={(v) => setEditStationType(v as StationType)} className="mt-1.5"
                options={[
                  { value: "closed", label: "תחנה סגורה" },
                  { value: "open", label: "תחנה פתוחה" },
                  { value: "switching", label: "תחנת מיתוג" },
                  { value: "combined", label: "תחנה משולבת" },
                  { value: "mobile_substation", label: "תחנת משנה ניידת" },
                ]}
              />
            </div>
            <div>
              <Label>מנהל פרויקט</Label>
              <SearchableSelect
                value={editManagerId}
                onValueChange={setEditManagerId}
                className="mt-1.5"
                options={users.map((u) => ({
                  value: u.id,
                  label: `${u.name} (${u.role === "admin" ? "מנהל מערכת" : "מנהל פרויקט"})`,
                }))}
              />
            </div>
            <div>
              <Label>רמת סיכון</Label>
              <SearchableSelect value={editRisk} onValueChange={(v) => setEditRisk(v as "high" | "medium" | "low")} className="mt-1.5"
                options={[
                  { value: "high", label: "סיכון גבוה" },
                  { value: "medium", label: "סיכון בינוני" },
                  { value: "low", label: "סיכון נמוך" },
                ]}
              />
            </div>
            {siteFieldConfig?.isVisible && (
              <div>
                <Label>{siteFieldConfig.displayName}{siteFieldConfig.isRequired && " *"}</Label>
                <SiteCombobox value={editSite} onValueChange={setEditSite} />
              </div>
            )}
            <div>
              <Label>שלבי הפרויקט *</Label>
              <div className="mt-1.5">
                <MultiStageSelect value={editStageIndexes} onChange={setEditStageIndexes} />
              </div>
              <p className="text-[11px] text-muted-foreground mt-1">ניתן לבחור מספר שלבים פעילים במקביל. ספירות בדשבורד נעשות לפי השלב הגבוה ביותר.</p>
            </div>
            <div>
              <Label>ציוד / נושאים קשורים</Label>
              <div className="space-y-2 mt-2 max-h-40 overflow-y-auto">
                {equipment.map((eq) => (
                  <label key={eq.id} className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={editEquipmentIds.includes(eq.id)}
                      onCheckedChange={() => toggleEditEquipment(eq.id)}
                    />
                    <span className="text-sm">{eq.name}</span>
                    <span className="text-xs text-muted-foreground">({eq.category})</span>
                  </label>
                ))}
              </div>
            </div>
            <Button onClick={saveProject} className="w-full">שמור שינויים</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Lessons Presentation Dialog */}
      <LessonsPresentationDialog
        open={presentationOpen}
        onOpenChange={setPresentationOpen}
        projectName={project.name}
        activeStages={(project.stageIndexes && project.stageIndexes.length > 0
          ? [...project.stageIndexes].sort((a, b) => a - b)
          : [project.stageIndex]
        ).map((i) => PROJECT_STAGES[i])}
        lessons={projectLessons.map((l) => ({
          id: l.id,
          title: l.title,
          description: l.description,
          recommendation: l.recommendation,
          stage: l.stage,
          category: l.category,
          date: l.date,
          eventDate: l.eventDate,
        }))}
      />

      {/* Recommended Lessons Document Dialog */}
      <RecommendedLessonsDialog
        open={recommendedOpen}
        onOpenChange={setRecommendedOpen}
        project={project}
        allLessons={lessons}
        implementations={implementations}
        stages={PROJECT_STAGES.map((s) => s)}
        defaultStages={(project.stageIndexes && project.stageIndexes.length > 0
          ? [...project.stageIndexes].sort((a, b) => a - b)
          : [project.stageIndex]
        ).map((i) => PROJECT_STAGES[i])}
        projectMetaById={new Map(projects.map((p) => [p.id, { projectType: p.projectType, stationType: p.stationType }]))}
      />

      {/* Stage suggestion dialog */}
      <Dialog open={stageConfirmOpen} onOpenChange={handleCancelSuggestedLessons}>
        <DialogContent dir="rtl" className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>אישור לקחים לשלב החדש</DialogTitle>
            <DialogDescription>
              בחר אילו לקחים מתוך המלצות ה-AI להוסיף לפרויקט. אם תסגור את החלון, לא יתווספו לקחים.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            {suggestedLessons.map((suggestion) => {
              const lesson = lessons.find((item) => item.id === suggestion.lessonId);
              if (!lesson) return null;

              const isSelected = selectedSuggestionIds.includes(suggestion.lessonId);
              return (
                <label
                  key={suggestion.lessonId}
                  className="flex items-start gap-3 rounded-xl border border-border bg-card p-4 cursor-pointer"
                >
                  <Checkbox
                    checked={isSelected}
                    onCheckedChange={(checked) => {
                      setSelectedSuggestionIds((prev) =>
                        checked
                          ? [...prev, suggestion.lessonId]
                          : prev.filter((id) => id !== suggestion.lessonId)
                      );
                    }}
                  />
                  <div className="flex-1 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium">{lesson.title}</p>
                        <p className="text-xs text-muted-foreground mt-1">
                          שלב: {lesson.stage} · {lesson.category}
                        </p>
                      </div>
                      <Badge variant="outline" className={riskColors[suggestion.priority]}>
                        {priorityLabels[suggestion.priority]}
                      </Badge>
                    </div>
                    <p className="text-sm text-muted-foreground">{suggestion.reason}</p>
                  </div>
                </label>
              );
            })}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button variant="outline" onClick={() => handleCancelSuggestedLessons(false)} disabled={isApplyingSuggestions}>
                ביטול
              </Button>
              <Button onClick={handleConfirmSuggestedLessons} disabled={isApplyingSuggestions}>
                {isApplyingSuggestions ? "מוסיף לקחים..." : "הוסף לקחים נבחרים"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Stage Timeline */}
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading">שלבי הפרויקט</CardTitle>
          {canChangeStage && <p className="text-xs text-muted-foreground">לחץ על שלב כדי לעדכן, ואז אשר ידנית אילו לקחים להוסיף</p>}
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-1 overflow-x-auto pb-2">
            {PROJECT_STAGES.map((stage, i) => {
              const activeStages = project.stageIndexes && project.stageIndexes.length > 0 ? project.stageIndexes : [project.stageIndex];
              const maxActive = Math.max(...activeStages);
              const isCurrent = activeStages.includes(i);
              const isCompleted = !isCurrent && i < maxActive;
              const isFuture = !isCurrent && i > maxActive;
              return (
                <div key={stage} className="flex items-center">
                  <button
                    onClick={() => void handleStageChange(i)}
                    disabled={!canChangeStage || isAnalyzing || isApplyingSuggestions}
                    className={`
                      relative flex flex-col items-center px-3 py-3 rounded-xl transition-all min-w-[90px]
                      ${canChangeStage ? "cursor-pointer hover:scale-105" : "cursor-default"}
                      ${isCurrent ? "bg-accent text-accent-foreground shadow-lg scale-105" : ""}
                      ${isCompleted ? "bg-success/10 text-success" : ""}
                      ${isFuture ? "bg-muted text-muted-foreground" : ""}
                    `}
                  >
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold mb-1
                      ${isCurrent ? "bg-accent-foreground/20" : ""}
                      ${isCompleted ? "bg-success/20" : ""}
                      ${isFuture ? "bg-muted-foreground/10" : ""}
                    `}>
                      {isCompleted ? <CheckCircle2 className="w-4 h-4" /> : i + 1}
                    </div>
                    <span className="text-[11px] font-medium text-center leading-tight">{stage}</span>
                  </button>
                  {i < PROJECT_STAGES.length - 1 && (
                    <ChevronLeft className={`w-4 h-4 shrink-0 mx-0.5 ${isCompleted ? "text-success" : "text-muted-foreground/30"}`} />
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Equipment / Topics */}
      <Card className="border-0 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Wrench className="w-5 h-5 text-accent" />
            ציוד ונושאים ({projectEquipment.length})
          </CardTitle>
          {canChangeStage && (
            <Button variant="outline" size="sm" onClick={() => setEditingEquipment(!editingEquipment)}>
              {editingEquipment ? "סיום" : "ערוך"}
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {editingEquipment ? (
            <div className="space-y-2">
              {equipment.map((eq) => (
                <label key={eq.id} className="flex items-center gap-2 p-2 rounded-lg hover:bg-muted/50 cursor-pointer">
                  <Checkbox
                    checked={project.equipmentIds.includes(eq.id)}
                    onCheckedChange={() => toggleEquipment(eq.id)}
                  />
                  <span className="text-sm">{eq.name}</span>
                  <span className="text-xs text-muted-foreground">({eq.category})</span>
                </label>
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {projectEquipment.length > 0 ? (
                projectEquipment.map((eq) => (
                  <Badge key={eq.id} variant="outline" className="bg-accent/5 text-accent border-accent/20">
                    {eq.name}
                  </Badge>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">לא שויכו פריטי ציוד/נושאים לפרויקט זה</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* AI Project Analysis */}
      {projectAnalysis && (
        <Card className="border-0 shadow-sm ring-2 ring-primary/20">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Brain className="w-5 h-5 text-primary" />
              ניתוח AI של הפרויקט
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Summary */}
            <div className="p-4 rounded-xl bg-primary/5 border border-primary/10">
              <p className="text-sm leading-relaxed">{projectAnalysis.summary}</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Strengths */}
              {projectAnalysis.strengths.length > 0 && (
                <div className="p-4 rounded-xl bg-success/5 border border-success/15 space-y-2">
                  <h4 className="text-sm font-semibold flex items-center gap-1.5 text-success">
                    <TrendingUp className="w-4 h-4" />
                    נקודות חוזק
                  </h4>
                  <ul className="space-y-1">
                    {projectAnalysis.strengths.map((s, i) => (
                      <li key={i} className="text-sm text-muted-foreground flex items-start gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5 text-success mt-0.5 shrink-0" />
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Weaknesses */}
              {projectAnalysis.weaknesses.length > 0 && (
                <div className="p-4 rounded-xl bg-destructive/5 border border-destructive/15 space-y-2">
                  <h4 className="text-sm font-semibold flex items-center gap-1.5 text-destructive">
                    <TrendingDown className="w-4 h-4" />
                    נקודות חולשה / סיכונים
                  </h4>
                  <ul className="space-y-1">
                    {projectAnalysis.weaknesses.map((w, i) => (
                      <li key={i} className="text-sm text-muted-foreground flex items-start gap-1.5">
                        <Shield className="w-3.5 h-3.5 text-destructive mt-0.5 shrink-0" />
                        {w}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* Recommendations */}
            {projectAnalysis.recommendations.length > 0 && (
              <div className="p-4 rounded-xl bg-accent/5 border border-accent/15 space-y-2">
                <h4 className="text-sm font-semibold flex items-center gap-1.5 text-accent">
                  <Lightbulb className="w-4 h-4 text-accent" />
                  המלצות לשלב הנוכחי
                </h4>
                <ul className="space-y-1">
                  {projectAnalysis.recommendations.map((r, i) => (
                    <li key={i} className="text-sm text-muted-foreground flex items-start gap-1.5">
                      <span className="text-accent font-bold mt-0.5 shrink-0">{i + 1}.</span>
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {/* AI Feedback button */}
            <div className="flex justify-end">
              <AIFeedbackDialog contextType="project" contextId={project.id} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* AI Suggested Lessons - Pending Response */}
      {pendingImpls.length > 0 && (
        <Card className="border-0 shadow-sm ring-2 ring-accent/30">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Brain className="w-5 h-5 text-accent" />
              לקחים להטמעה — נדרשת תגובתך ({pendingImpls.length})
            </CardTitle>
            <p className="text-xs text-muted-foreground">AI זיהה לקחים רלוונטיים לפרויקט בשלב הנוכחי. נא להגיב לכל לקח.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            {pendingImpls.map((impl) => (
              <div key={impl.id} className="p-4 rounded-xl bg-accent/5 border border-accent/20 space-y-3">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <h4 className="text-sm font-medium cursor-pointer hover:text-accent transition-colors" onClick={() => navigate(`/lessons/${impl.lessonId}`)}>{getLessonTitle(impl.lessonId)}</h4>
                    <p className="text-xs text-muted-foreground mt-1">
                      <span className="text-accent font-medium">סיבת הרלוונטיות:</span> {impl.reason}
                    </p>
                  </div>
                  <Badge variant="outline" className={riskColors[impl.priority]}>
                    {priorityLabels[impl.priority]}
                  </Badge>
                </div>

                <div className="space-y-2">
                  <Textarea
                    placeholder="הערות נוספות (אופציונלי)..."
                    value={feedbackNotes[impl.id] || ""}
                    onChange={(e) => setFeedbackNotes((prev) => ({ ...prev, [impl.id]: e.target.value }))}
                    rows={2}
                    className="text-sm"
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      className="bg-success text-success-foreground hover:bg-success/90 gap-1.5"
                      onClick={() => handleRespond(impl.id, true, true)}
                    >
                      <ThumbsUp className="w-3.5 h-3.5" />
                      רלוונטי ויושם
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1.5 border-info text-info hover:bg-info/10"
                      onClick={() => handleRespond(impl.id, true, false)}
                    >
                      <MessageSquare className="w-3.5 h-3.5" />
                      רלוונטי, לא יושם עדיין
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1.5 border-destructive text-destructive hover:bg-destructive/10"
                      onClick={() => handleRespond(impl.id, false, false)}
                    >
                      <ThumbsDown className="w-3.5 h-3.5" />
                      לא רלוונטי
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Answered Implementations */}
      {answeredImpls.length > 0 && (
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base font-heading">לקחים שנבדקו ({answeredImpls.length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {answeredImpls.map((impl) => {
              const responder = users.find((u) => u.id === impl.respondedBy);
              return (
                <div key={impl.id} className="flex items-center justify-between p-3 rounded-xl bg-muted/50">
                  <div className="flex-1">
                    <p className="text-sm font-medium cursor-pointer hover:text-accent transition-colors" onClick={() => navigate(`/lessons/${impl.lessonId}`)}>{getLessonTitle(impl.lessonId)}</p>
                    {impl.notes && <p className="text-xs text-muted-foreground mt-0.5">הערות: {impl.notes}</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className={impl.isRelevant ? "bg-success/10 text-success border-success/20" : "bg-destructive/10 text-destructive border-destructive/20"}>
                      {impl.isRelevant ? (impl.isImplemented ? "יושם ✓" : "רלוונטי") : "לא רלוונטי"}
                    </Badge>
                    {responder && <span className="text-[11px] text-muted-foreground">{responder.name}</span>}
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {/* Project Lessons */}
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-base font-heading">
            <span className="flex items-center gap-2">
              <BookOpen className="w-5 h-5 text-accent" />
              לקחים בפרויקט ({projectLessons.length})
            </span>
            {hasPermission("add_lessons") && project && (
              <Button
                size="sm"
                className="bg-accent text-accent-foreground hover:bg-accent/90 gap-2"
                onClick={() => navigate(`/lessons?newLesson=${project.id}`)}
              >
                <Plus className="w-4 h-4" />
                לקח חדש
              </Button>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {projectLessons.length > 0 ? (
            <div className="space-y-3">
              {projectLessons.map((lesson) => (
                <div key={lesson.id} className="flex items-center justify-between p-4 rounded-xl bg-muted/50 hover:bg-muted transition-colors cursor-pointer" onClick={() => navigate(`/lessons/${lesson.id}`)}>
                  <div className="flex-1">
                    <p className="text-sm font-medium">{lesson.title}</p>
                    <p className="text-xs text-muted-foreground mt-1">שלב: {lesson.stage} · {lesson.category} · {lesson.date}</p>
                  </div>
                  <span className="text-xs text-muted-foreground">{statusLabels[lesson.status]}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground text-center py-8">אין לקחים עדיין בפרויקט זה</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default ProjectDetail;
