import { useState, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowRight, BookOpen, FolderKanban, Wrench, ThumbsUp, ThumbsDown, MessageSquare, Brain, Pencil, Save, X, RefreshCw, Loader2, PlusCircle, Trash2, CalendarIcon, FileText, ImageIcon, Clock, AlertTriangle as AlertTriangleIcon, RotateCcw, Check } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import SearchableSelect from "@/components/ui/searchable-select";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useUser, PROJECT_STAGES, projectTypeLabels, stationTypeLabels } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";
import { riskColors, riskLabels, statusLabels, statusColors, PROFESSIONAL_DOMAINS, WORKFLOW_STATUSES, workflowStatusColors, REFERENT_DECISIONS, referentDecisionColors } from "@/lib/constants";
import { supabase } from "@/integrations/supabase/client";
import { useActionRequired } from "@/hooks/useActionRequired";
import { Zap } from "lucide-react";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { format, parseISO } from "date-fns";
import { cn } from "@/lib/utils";
import LessonTimeline from "@/components/LessonTimeline";
import LessonWorkflowHistory from "@/components/LessonWorkflowHistory";

import ReturnLessonDialog from "@/components/ReturnLessonDialog";
import { useFieldLabels } from "@/hooks/useFieldLabels";

const LessonDetail = () => {
  const { getLabel } = useFieldLabels("lesson");
  const { id } = useParams();
  const navigate = useNavigate();
  const { lessons, projects, equipment, users, implementations, referentReviews, respondToImplementation, respondToReferentReview, currentUser, updateLesson, updateLessonAIReview, deleteLesson, updateWorkflowStatus, updateReferentDecision, returnLessonToCreator, lessonCategories: categories } = useUser();
  const [feedbackNotes, setFeedbackNotes] = useState<Record<string, string>>({});
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editRecommendation, setEditRecommendation] = useState("");
  const [editStage, setEditStage] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editRisk, setEditRisk] = useState<"high" | "medium" | "low">("low");
  const [editEquipmentIds, setEditEquipmentIds] = useState<number[]>([]);
  const [editProfessionalDomain, setEditProfessionalDomain] = useState("");
  const [editEventDate, setEditEventDate] = useState<Date | undefined>(undefined);
  const [editAssignedTo, setEditAssignedTo] = useState("");
  const [editWorkflowStatus, setEditWorkflowStatus] = useState("");
  const [editImpactScheduleDelay, setEditImpactScheduleDelay] = useState("");
  const [editImpactBudgetCost, setEditImpactBudgetCost] = useState("");
  const [editImpactQualityDesc, setEditImpactQualityDesc] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isReAnalyzing, setIsReAnalyzing] = useState(false);
  const [isAddingToProject, setIsAddingToProject] = useState(false);
  const [referentDecision, setReferentDecision] = useState("");
  const [referentResponseText, setReferentResponseText] = useState("");
  const [adminReturnNotes, setAdminReturnNotes] = useState("");
  const [completionNote, setCompletionNote] = useState("");

  const [returnDialogOpen, setReturnDialogOpen] = useState(false);
  const [returnSource, setReturnSource] = useState<"referent" | "admin">("admin");

  const { lessonNeedsAction } = useActionRequired(lessons, implementations, projects, currentUser, referentReviews);

  const lesson = lessons.find((l) => l.id === Number(id));

  // Projects the current PM can manually add this lesson to
  const addableProjects = useMemo(() => {
    if (!lesson) return [];
    if (lesson.status !== "approved" && lesson.status !== "distributed") return [];
    if (currentUser.role !== "project_manager") return [];
    const alreadyLinkedIds = new Set([
      ...(lesson.distributedTo || []),
      ...(lesson.projectId ? [lesson.projectId] : []),
    ]);
    return projects.filter(
      (p) => currentUser.assignedProjects.includes(p.id) && !alreadyLinkedIds.has(p.id)
    );
  }, [lesson, currentUser, projects]);

  // Referent can self-add approved/distributed lessons
  const canReferentSelfAdd = useMemo(() => {
    if (!lesson) return false;
    if (currentUser.role !== "referent") return false;
    if (lesson.status !== "approved" && lesson.status !== "distributed") return false;
    // Already distributed to this referent
    if (lesson.distributedToReferents?.includes(currentUser.id)) return false;
    return true;
  }, [lesson, currentUser]);

  if (!lesson) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">הלקח לא נמצא</p>
        <Button variant="ghost" onClick={() => navigate("/lessons")} className="mt-4 gap-2">
          <ArrowRight className="w-4 h-4" />
          חזרה ללקחים
        </Button>
      </div>
    );
  }

  const canEdit = currentUser.role === "admin" || lesson.createdBy === currentUser.id;

  const creator = users.find((u) => u.id === lesson.createdBy);
  const approver = lesson.approvedBy ? users.find((u) => u.id === lesson.approvedBy) : null;
  const lessonEquipment = equipment.filter((e) => lesson.equipmentIds.includes(e.id));
  const lessonImpls = implementations.filter((i) => i.lessonId === lesson.id);
  const lessonRefReviews = referentReviews.filter((r) => r.lessonId === lesson.id);
  const distributedProjects = lesson.distributedTo
    ? projects.filter((p) => lesson.distributedTo!.includes(p.id))
    : [];
  const distributedReferents = lesson.distributedToReferents
    ? users.filter((u) => lesson.distributedToReferents!.includes(u.id))
    : [];

  const handleAddToProject = async (projectId: number) => {
    setIsAddingToProject(true);
    try {
      const project = projects.find((p) => p.id === projectId);
      if (!project) return;

      // Update distributed_to
      const newDistributedTo = [...(lesson.distributedTo || []), projectId];
      await supabase.from("lessons").update({
        distributed_to: newDistributedTo,
        status: "distributed",
      }).eq("id", lesson.id);

      // Create implementation record
      const { data: implData } = await supabase.from("lesson_implementations").insert({
        lesson_id: lesson.id,
        project_id: projectId,
        reason: "נוסף ידנית ע״י מנהל הפרויקט",
        priority: "medium",
      }).select().single();

      // Update local state via context - refresh
      window.location.reload();
    } catch (e) {
      console.error("Error adding lesson to project:", e);
      toast({ title: "שגיאה בהוספת הלקח לפרויקט", variant: "destructive" });
    } finally {
      setIsAddingToProject(false);
    }
  };

  const handleStartEdit = () => {
    setEditTitle(lesson.title);
    setEditDesc(lesson.description);
    setEditRecommendation(lesson.recommendation);
    setEditStage(lesson.stage);
    setEditCategory(lesson.category);
    setEditRisk(lesson.risk);
    setEditEquipmentIds(lesson.equipmentIds);
    setEditProfessionalDomain(lesson.professionalDomain || "");
    setEditEventDate(lesson.eventDate ? parseISO(lesson.eventDate) : undefined);
    setEditAssignedTo(lesson.assignedTo || "");
    setEditWorkflowStatus(lesson.workflowStatus || "חדש");
    setEditImpactScheduleDelay(lesson.impactScheduleDelay?.toString() || "");
    setEditImpactBudgetCost(lesson.impactBudgetCost?.toString() || "");
    setEditImpactQualityDesc(lesson.impactQualityDesc || "");
    setIsEditing(true);
  };

  const handleSave = async () => {
    if (!editTitle.trim()) {
      toast({ title: "שגיאה", description: "כותרת הלקח לא יכולה להיות ריקה", variant: "destructive" });
      return;
    }
    setIsSaving(true);
    try {
      await updateLesson(lesson.id, {
        title: editTitle,
        description: editDesc,
        recommendation: editRecommendation,
        stage: editStage,
        category: editCategory,
        risk: editRisk as "high" | "medium" | "low",
        equipmentIds: editEquipmentIds,
        professionalDomain: editProfessionalDomain || undefined,
        eventDate: editEventDate ? format(editEventDate, "yyyy-MM-dd") : undefined,
        assignedTo: editAssignedTo || undefined,
        workflowStatus: editWorkflowStatus,
        impactScheduleDelay: editImpactScheduleDelay ? Number(editImpactScheduleDelay) : undefined,
        impactBudgetCost: editImpactBudgetCost ? Number(editImpactBudgetCost) : undefined,
        impactQualityDesc: editImpactQualityDesc || undefined,
      });
      toast({ title: "הלקח עודכן בהצלחה ✓" });
      setIsEditing(false);
    } catch {
      toast({ title: "שגיאה בעדכון", variant: "destructive" });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRespond = (implId: number, isRelevant: boolean, isImplemented: boolean) => {
    respondToImplementation(implId, {
      isRelevant,
      isImplemented,
      notes: feedbackNotes[implId] || "",
    });
    toast({ title: "תגובה נשמרה ✓" });
  };

  const handleReferentRespond = (reviewId: number, isRelevant: boolean, isImplemented: boolean) => {
    respondToReferentReview(reviewId, {
      isRelevant,
      isImplemented,
      notes: feedbackNotes[`ref_${reviewId}`] || "",
    });
    toast({ title: "תגובה נשמרה ✓" });
  };

  const handleReAnalyze = async () => {
    setIsReAnalyzing(true);
    try {
      const lessonEquipmentNames = equipment.filter((e) => lesson.equipmentIds.includes(e.id)).map((e) => e.name);
      const activeProjects = projects
        .filter((p) => p.id !== lesson.projectId)
        .map((p) => ({
          id: p.id,
          name: p.name,
          stage: PROJECT_STAGES[p.stageIndex],
          type: projectTypeLabels[p.projectType],
          station: stationTypeLabels[p.stationType],
          equipment: equipment.filter((eq) => p.equipmentIds.includes(eq.id)).map((eq) => eq.name),
        }));

      // Collect feedback from implementations
      const feedbackData = lessonImpls
        .filter((impl) => impl.respondedBy)
        .map((impl) => {
          const project = projects.find((p) => p.id === impl.projectId);
          const responder = users.find((u) => u.id === impl.respondedBy);
          return {
            projectName: project?.name || "",
            respondedBy: responder?.name || "",
            isRelevant: impl.isRelevant,
            isImplemented: impl.isImplemented,
            notes: impl.notes || "",
          };
        });

      const { data, error } = await supabase.functions.invoke("review-lesson", {
        body: {
          type: "re_analyze",
          lesson: {
            title: lesson.title,
            description: lesson.description,
            projectName: lesson.project,
            stage: lesson.stage,
            category: lesson.category,
            risk: lesson.risk,
            equipmentNames: lessonEquipmentNames,
          },
          projects: activeProjects,
          feedback: feedbackData,
        },
      });
      if (error) throw error;

      const review = data?.review;
      if (review) {
        updateLessonAIReview(lesson.id, review);
        toast({ title: "ניתוח AI עודכן בהצלחה ✓", description: "הניתוח עודכן בהתאם לפידבק מנהלי הפרויקטים" });
      }
    } catch (e) {
      console.error("Re-analyze error:", e);
      toast({ title: "שגיאה בניתוח AI", variant: "destructive" });
    } finally {
      setIsReAnalyzing(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/lessons")}>
          <ArrowRight className="w-5 h-5" />
        </Button>
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
            <BookOpen className="w-6 h-6 text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl md:text-2xl font-heading font-bold">{lesson.title}</h1>
            <div className="flex items-center gap-2 mt-1 flex-wrap text-sm text-muted-foreground">
              <span>{lesson.project}</span>
              <span>·</span>
              <span>{lesson.stage}</span>
              <span>·</span>
              <span>{lesson.category}</span>
              <Badge variant="outline" className={`mr-1 ${statusColors[lesson.status]}`}>{statusLabels[lesson.status]}</Badge>
              <Badge variant="outline" className={riskColors[lesson.risk]}>{riskLabels[lesson.risk]}</Badge>
            </div>
          </div>
          {canEdit && !isEditing && (
            <div className="flex gap-1.5 shrink-0">
              <Button variant="outline" size="sm" className="gap-1.5" onClick={handleStartEdit}>
                <Pencil className="w-4 h-4" />
                עריכה
              </Button>
              {(currentUser.role === "admin" || lesson.createdBy === currentUser.id) && (
                <Button variant="outline" size="sm" className="gap-1.5 text-destructive border-destructive/30 hover:bg-destructive/10" onClick={() => {
                  deleteLesson(lesson.id);
                  toast({ title: "לקח נמחק" });
                  navigate("/lessons");
                }}>
                  <Trash2 className="w-4 h-4" />
                  מחק
                </Button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Action Required Banner */}
      {lessonNeedsAction(lesson.id).needed && !isEditing && (
        <Alert className="border-warning/30 bg-warning/5">
          <Zap className="w-4 h-4 text-warning" />
          <AlertTitle className="text-warning font-semibold">דורש פעולה</AlertTitle>
          <AlertDescription className="text-sm text-muted-foreground">
            {currentUser.role === "admin" && lesson.status === "new" && "לקח זה ממתין לאישור או דחייה שלך."}
            {currentUser.role === "admin" && lesson.status === "approved" && (!lesson.distributedTo || lesson.distributedTo.length === 0) && "לקח זה אושר אך טרם הופץ לפרויקטים. הפץ אותו כדי שמנהלי הפרויקטים יוכלו ליישם."}
            {currentUser.role === "project_manager" && lesson.status === "rejected" && lesson.createdBy === currentUser.id && "לקח זה נדחה. ערוך ושלח מחדש."}
            {currentUser.role === "project_manager" && lessonNeedsAction(lesson.id).label === "ממתין לתגובה" && "יש implementation של לקח זה בפרויקט שלך שממתין לתגובה. גלול מטה כדי להגיב."}
            {currentUser.role === "referent" && lessonNeedsAction(lesson.id).label === "ממתין לתגובה" && "לקח זה הופץ אליך כרפרנט. גלול מטה כדי להגיב."}
          </AlertDescription>
        </Alert>
      )}

      {/* Workflow Timeline & Transparency */}
      {!isEditing && (
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Clock className="w-5 h-5 text-primary" />
              מצב הלקח
            </CardTitle>
          </CardHeader>
          <CardContent>
            <LessonTimeline lesson={lesson} users={users} />
          </CardContent>
        </Card>
      )}

      {/* Full workflow trail — visible to anyone allowed to view the lesson */}
      {!isEditing && <LessonWorkflowHistory lessonId={lesson.id} />}


      {/* Edit Form */}
      {isEditing && (
        <Card className="border-2 border-primary/20 shadow-sm">
          <CardContent className="p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold flex items-center gap-2">
                <Pencil className="w-4 h-4 text-primary" />
                עריכת לקח
              </h3>
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => setIsEditing(false)} disabled={isSaving}>
                  <X className="w-4 h-4 ml-1" />
                  ביטול
                </Button>
                <Button size="sm" onClick={handleSave} disabled={isSaving} className="gap-1.5">
                  <Save className="w-4 h-4" />
                  {isSaving ? "שומר..." : "שמור"}
                </Button>
              </div>
            </div>

            <div className="space-y-4">
              {/* פרטי הלקח */}
              <h4 className="text-xs font-semibold text-primary border-b border-border pb-1">פרטי הלקח</h4>
              <div>
                <Label className="text-xs">{getLabel("title", "כותרת")}</Label>
                <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
              </div>
              <div>
                <Label className="text-xs">{getLabel("description", "תיאור")}</Label>
                <Textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)} rows={3} />
              </div>
              <div>
                <Label className="text-xs">{getLabel("recommendation", "המלצה")}</Label>
                <Textarea value={editRecommendation} onChange={(e) => setEditRecommendation(e.target.value)} rows={2} />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <Label className="text-xs">{getLabel("stage", "שלב")}</Label>
                  <SearchableSelect value={editStage} onValueChange={setEditStage}
                    options={[
                      ...PROJECT_STAGES.map((s) => ({ value: String(s), label: s })),
                    ]}
                  />
                </div>
                <div>
                  <Label className="text-xs">{getLabel("category", "קטגוריה")}</Label>
                  <SearchableSelect value={editCategory} onValueChange={setEditCategory}
                    options={[
                      ...categories.map((c) => ({ value: String(c), label: c })),
                    ]}
                  />
                </div>
                <div>
                  <Label className="text-xs">{getLabel("risk", "רמת סיכון")}</Label>
                  <SearchableSelect value={editRisk} onValueChange={(v) => setEditRisk(v as "high" | "medium" | "low")}
                    options={[
                      { value: "high", label: "גבוה" },
                      { value: "medium", label: "בינוני" },
                      { value: "low", label: "נמוך" },
                    ]}
                  />
                </div>
                <div>
                  <Label className="text-xs">{getLabel("professional_domain", "תחום מקצועי / מגזר")}</Label>
                  <SearchableSelect value={editProfessionalDomain} onValueChange={setEditProfessionalDomain} placeholder="בחר תחום"
                    options={[
                      ...PROFESSIONAL_DOMAINS.map((d) => ({ value: String(d), label: d })),
                    ]}
                  />
                </div>
                <div>
                  <Label className="text-xs">{getLabel("event_date", "תאריך האירוע")}</Label>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Button variant="outline" className={cn("w-full justify-start text-right font-normal text-xs", !editEventDate && "text-muted-foreground")}>
                        <CalendarIcon className="ml-2 h-3.5 w-3.5" />
                        {editEventDate ? format(editEventDate, "dd/MM/yyyy") : "בחר תאריך"}
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-auto p-0" align="start">
                      <Calendar mode="single" selected={editEventDate} onSelect={setEditEventDate} initialFocus className={cn("p-3 pointer-events-auto")} />
                    </PopoverContent>
                  </Popover>
                </div>
              </div>

              {/* השפעה */}
              <h4 className="text-xs font-semibold text-primary border-b border-border pb-1 mt-2">השפעה</h4>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">{getLabel("impact_schedule_delay", "השפעה על לו״ז – הערכת עיכוב (ימים)")}</Label>
                  <Input type="number" value={editImpactScheduleDelay} onChange={(e) => setEditImpactScheduleDelay(e.target.value)} placeholder="מספר ימים" />
                </div>
                <div>
                  <Label className="text-xs">{getLabel("impact_budget_cost", "השפעה על תקציב – הערכת עלות (₪)")}</Label>
                  <Input type="number" value={editImpactBudgetCost} onChange={(e) => setEditImpactBudgetCost(e.target.value)} placeholder="סכום ב-₪" />
                </div>
              </div>
              <div>
                <Label className="text-xs">{getLabel("impact_quality_desc", "השפעה על איכות – תיאור השפעה")}</Label>
                <Textarea value={editImpactQualityDesc} onChange={(e) => setEditImpactQualityDesc(e.target.value)} rows={2} placeholder="תאר את ההשפעה על האיכות..." />
              </div>

              {/* ציוד / נושאים */}
              {equipment.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-primary border-b border-border pb-1 mt-2 mb-2">ציוד / נושאים</h4>
                  <div className="flex flex-wrap gap-2">
                    {equipment.map((eq) => (
                      <label key={eq.id} className="flex items-center gap-1.5 text-xs cursor-pointer">
                        <Checkbox
                          checked={editEquipmentIds.includes(eq.id)}
                          onCheckedChange={(checked) => {
                            setEditEquipmentIds((prev) =>
                              checked ? [...prev, eq.id] : prev.filter((id) => id !== eq.id)
                            );
                          }}
                        />
                        {eq.name}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              {/* טיפול ומעקב */}
              <h4 className="text-xs font-semibold text-primary border-b border-border pb-1 mt-2">טיפול ומעקב</h4>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">{getLabel("assigned_to", "אחראי ליישום")}</Label>
                  <SearchableSelect value={editAssignedTo} onValueChange={setEditAssignedTo} placeholder="בחר משתמש"
                    options={[
                      ...users.map((u) => ({ value: String(u.id), label: u.name })),
                    ]}
                  />
                </div>
                <div>
                  <Label className="text-xs">{getLabel("workflow_status", "WF – סטטוס טיפול")}</Label>
                  <SearchableSelect value={editWorkflowStatus} onValueChange={setEditWorkflowStatus}
                    options={[
                      ...WORKFLOW_STATUSES.map((s) => ({ value: String(s), label: s })),
                    ]}
                  />
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Details Card */}
      {!isEditing && (
        <Card className="border-0 shadow-sm">
          <CardContent className="p-6 space-y-5">
            {/* פרטי הלקח */}
            <h4 className="text-sm font-semibold text-primary border-b border-border pb-1">פרטי הלקח</h4>
            <div>
              <h3 className="text-sm font-semibold mb-1">תיאור</h3>
              <p className="text-sm text-muted-foreground">{lesson.description}</p>
            </div>
            {lesson.recommendation && (
              <div>
                <h3 className="text-sm font-semibold mb-1">המלצה</h3>
                <p className="text-sm text-muted-foreground">{lesson.recommendation}</p>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">נוצר ע״י</span>
                <p className="font-medium">{creator?.name || lesson.createdBy}</p>
              </div>
              <div>
                <span className="text-muted-foreground">תאריך יצירה</span>
                <p className="font-medium">{lesson.date}</p>
              </div>
              {approver && (
                <div>
                  <span className="text-muted-foreground">אושר ע״י</span>
                  <p className="font-medium">{approver.name}</p>
                </div>
              )}
              <div>
                <span className="text-muted-foreground">פרויקט מקור</span>
                <p className="font-medium">{lesson.project}</p>
              </div>
              {lesson.professionalDomain && (
                <div>
                  <span className="text-muted-foreground">תחום מקצועי / מגזר</span>
                  <p className="font-medium">{lesson.professionalDomain}</p>
                </div>
              )}
              {lesson.eventDate && (
                <div>
                  <span className="text-muted-foreground">תאריך האירוע</span>
                  <p className="font-medium">{lesson.eventDate}</p>
                </div>
              )}
            </div>

            {/* השפעה */}
            {(lesson.impactScheduleDelay !== undefined || lesson.impactBudgetCost !== undefined || lesson.impactQualityDesc) && (
              <>
                <h4 className="text-sm font-semibold text-primary border-b border-border pb-1">השפעה</h4>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
                  {lesson.impactScheduleDelay !== undefined && (
                    <div>
                      <span className="text-muted-foreground">השפעה על לו״ז</span>
                      <p className="font-medium">{lesson.impactScheduleDelay} ימים</p>
                    </div>
                  )}
                  {lesson.impactBudgetCost !== undefined && (
                    <div>
                      <span className="text-muted-foreground">השפעה על תקציב</span>
                      <p className="font-medium">₪{lesson.impactBudgetCost.toLocaleString()}</p>
                    </div>
                  )}
                  {lesson.impactQualityDesc && (
                    <div className="col-span-full">
                      <span className="text-muted-foreground">השפעה על איכות</span>
                      <p className="font-medium">{lesson.impactQualityDesc}</p>
                    </div>
                  )}
                </div>
              </>
            )}

            {/* ציוד / נושאים */}
            {lessonEquipment.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold mb-1.5 flex items-center gap-1.5">
                  <Wrench className="w-4 h-4" />
                  ציוד / נושאים קשורים
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {lessonEquipment.map((eq) => (
                    <Badge key={eq.id} variant="outline" className="bg-accent/5 border-accent/20">
                      {eq.name}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* קובץ מצורף */}
            {lesson.fileUrl && (
              <div>
                <h3 className="text-sm font-semibold mb-1.5 flex items-center gap-1.5">
                  <FileText className="w-4 h-4" />
                  קובץ מצורף
                </h3>
                {lesson.fileUrl.match(/\.(jpg|jpeg|png|gif|webp)$/i) ? (
                  <a href={lesson.fileUrl} target="_blank" rel="noopener noreferrer" className="block">
                    <img src={lesson.fileUrl} alt="קובץ מצורף" className="max-h-48 rounded-lg border border-border object-contain" />
                  </a>
                ) : (
                  <a href={lesson.fileUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline">
                    <FileText className="w-4 h-4" />
                    צפה בקובץ
                  </a>
                )}
              </div>
            )}

            {/* טיפול ומעקב */}
            <>
              <h4 className="text-sm font-semibold text-primary border-b border-border pb-1">טיפול ומעקב</h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                {lesson.assignedTo && (
                  <div>
                    <span className="text-muted-foreground">אחראי ליישום</span>
                    <p className="font-medium">{users.find((u) => u.id === lesson.assignedTo)?.name || lesson.assignedTo}</p>
                  </div>
                )}
                <div>
                  <span className="text-muted-foreground">WF – סטטוס טיפול</span>
                  <Badge variant="outline" className={`mt-1 ${workflowStatusColors[lesson.workflowStatus] || ""}`}>
                    {lesson.workflowStatus}
                  </Badge>
                </div>
                {lesson.targetDate && (
                  <div>
                    <span className="text-muted-foreground">תאריך יעד לטיפול</span>
                    <p className="font-medium">{lesson.targetDate}</p>
                  </div>
                )}
                {lesson.referentStartDate && (
                  <div>
                    <span className="text-muted-foreground">תחילת טיפול רפרנט</span>
                    <p className="font-medium">{lesson.referentStartDate}</p>
                  </div>
                )}
                {lesson.closedDate && (
                  <div>
                    <span className="text-muted-foreground">תאריך סגירה</span>
                    <p className="font-medium">{lesson.closedDate}</p>
                  </div>
                )}
              </div>
              {/* SLA warning */}
              {lesson.referentStartDate && !lesson.closedDate && lesson.workflowStatus !== "נסגר" && lesson.workflowStatus !== "נדחה" && (() => {
                const startDate = new Date(lesson.referentStartDate);
                const now = new Date();
                const daysDiff = Math.floor((now.getTime() - startDate.getTime()) / (1000 * 60 * 60 * 24));
                if (daysDiff >= 7) {
                  return (
                    <div className={`flex items-center gap-2 p-2.5 rounded-lg ${daysDiff >= 10 ? "bg-destructive/10 border border-destructive/20" : "bg-warning/10 border border-warning/20"}`}>
                      <Clock className={`w-4 h-4 ${daysDiff >= 10 ? "text-destructive" : "text-warning"}`} />
                      <span className={`text-xs font-medium ${daysDiff >= 10 ? "text-destructive" : "text-warning"}`}>
                        {daysDiff >= 10 ? `⚠️ חריגת SLA – ${daysDiff} ימים מתחילת הטיפול (מקסימום 10)` : `⏰ תזכורת – ${daysDiff} ימים מתחילת הטיפול`}
                      </span>
                    </div>
                  );
                }
                return null;
              })()}
            </>
          </CardContent>
        </Card>
      )}

      {/* Creator Completion Action — when lesson is returned to creator */}
      {lesson.workflowStatus === "ממתין להשלמת יוצר" && lesson.createdBy === currentUser.id && !isEditing && (
        <Card className="border-0 shadow-sm border-r-4 border-r-accent">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <RefreshCw className="w-5 h-5 text-accent" />
              נדרשת השלמה
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {lesson.returnReason && (
              <div className="p-3 rounded-lg bg-warning/10 border border-warning/20 text-sm">
                <p className="font-medium text-warning">סיבת ההחזרה:</p>
                <p className="text-muted-foreground mt-1">{lesson.returnReason}</p>
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              ערוך את הלקח בהתאם לדרישות ולאחר מכן לחץ על "סיימתי להשלים" כדי להחזיר לטיפול.
            </p>
            <div className="space-y-1.5">
              <Label className="text-sm">תיאור ההשלמה (יישמר בהיסטוריית הטיפול)</Label>
              <Textarea
                value={completionNote}
                onChange={(e) => setCompletionNote(e.target.value)}
                placeholder="מה השלמת בעקבות הבקשה?"
                rows={2}
              />
            </div>
            <div className="flex gap-2">
              <Button size="sm" className="gap-1.5" onClick={handleStartEdit}>
                <Pencil className="w-4 h-4" />
                ערוך לקח
              </Button>
              <Button
                size="sm"
                className="bg-success text-success-foreground hover:bg-success/90 gap-1.5"
                onClick={async () => {
                  // Return to whoever requested the completion
                  const returnTo = lesson.returnedBy === "referent" ? "בטיפול רפרנט" : "ממתין לאישור מנהל מערכת";
                  await updateWorkflowStatus(
                    lesson.id,
                    returnTo,
                    completionNote.trim() || "היוצר סימן שהשלים את הנדרש",
                    "creator_completed"
                  );
                  setCompletionNote("");
                  toast({ title: "הלקח הוחזר לטיפול ✓", description: returnTo === "בטיפול רפרנט" ? "הלקח הועבר חזרה לרפרנט" : "הלקח הועבר חזרה למנהל המערכת" });
                }}
              >

                <Check className="w-4 h-4" />
                סיימתי להשלים
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Referent Workflow Actions */}
      {(currentUser.role === "referent" || currentUser.id === lesson.assignedTo) && lesson.workflowStatus === "בטיפול רפרנט" && lessonRefReviews.some((r) => r.referentId === currentUser.id) && (
        <Card className="border-0 shadow-sm border-r-4 border-r-warning">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <BookOpen className="w-5 h-5 text-warning" />
              טיפול רפרנט
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label className="text-sm">החלטת רפרנט</Label>
              <SearchableSelect value={referentDecision} onValueChange={setReferentDecision} placeholder="בחר החלטה"
                options={[
                  ...REFERENT_DECISIONS.map((d) => ({ value: String(d), label: d })),
                ]}
              />
            </div>
            <div className="space-y-2">
              <Label className="text-sm">התייחסות רפרנט</Label>
              <Textarea
                value={referentResponseText}
                onChange={(e) => setReferentResponseText(e.target.value)}
                placeholder="מה נבדק? מה נעשה? האם נדרש טיפול נוסף? האם הוקם צוות?"
                rows={3}
              />
            </div>
            <div className="flex gap-2 flex-wrap">
              <Button
                size="sm"
                className="gap-1.5"
                disabled={!referentDecision || !referentResponseText.trim()}
                onClick={async () => {
                  const review = lessonRefReviews.find((r) => r.referentId === currentUser.id && r.lessonId === lesson.id);
                  if (!review) return;
                  await updateReferentDecision(review.id, referentDecision, referentResponseText);
                  if (referentDecision === "לא רלוונטי" || referentDecision === "הלקח נדחה") {
                    await updateWorkflowStatus(lesson.id, "נדחה");
                    toast({ title: "הלקח נדחה" });
                  } else {
                    await updateWorkflowStatus(lesson.id, "ממתין לאישור מנהל מערכת");
                    toast({ title: "הלקח הועבר לאישור מנהל מערכת ✓" });
                  }
                  setReferentDecision("");
                  setReferentResponseText("");
                }}
              >
                שלח התייחסות
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => { setReturnSource("referent"); setReturnDialogOpen(true); }}
              >
                <RotateCcw className="w-3.5 h-3.5" />
                החזר ליוצר להשלמה
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Admin Workflow Actions */}
      {currentUser.role === "admin" && lesson.workflowStatus === "ממתין לאישור מנהל מערכת" && (
        <Card className="border-0 shadow-sm border-r-4 border-r-primary">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <BookOpen className="w-5 h-5 text-primary" />
              אישור מנהל מערכת
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Show referent decisions */}
            {lessonRefReviews.filter((r) => r.decision).map((review) => {
              const referent = users.find((u) => u.id === review.referentId);
              return (
                <div key={review.id} className="p-3 rounded-lg bg-muted/50 text-sm space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{referent?.name}</span>
                    <Badge variant="outline" className={referentDecisionColors[review.decision || ""] || ""}>{review.decision}</Badge>
                  </div>
                  {review.responseText && <p className="text-xs text-muted-foreground">{review.responseText}</p>}
                </div>
              );
            })}
            <div className="space-y-2">
              <Label className="text-sm">הערות (אופציונלי)</Label>
              <Textarea
                value={adminReturnNotes}
                onChange={(e) => setAdminReturnNotes(e.target.value)}
                placeholder="הערות..."
                rows={2}
              />
            </div>
            <div className="flex gap-2 flex-wrap">
              <Button
                size="sm"
                className="bg-success text-success-foreground hover:bg-success/90 gap-1.5"
                onClick={async () => {
                  await updateWorkflowStatus(lesson.id, "אושר והופץ", adminReturnNotes);
                  toast({ title: "הלקח אושר והופץ ✓" });
                  setAdminReturnNotes("");
                }}
              >
                אשר והפץ
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 border-destructive text-destructive"
                onClick={async () => {
                  await updateWorkflowStatus(lesson.id, "נדחה", adminReturnNotes);
                  toast({ title: "הלקח נדחה" });
                  setAdminReturnNotes("");
                }}
              >
                דחה
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 border-warning text-warning"
                onClick={async () => {
                  await updateWorkflowStatus(lesson.id, "בטיפול רפרנט", adminReturnNotes);
                  toast({ title: "הלקח הוחזר לרפרנט" });
                  setAdminReturnNotes("");
                }}
              >
                החזר לרפרנט
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => { setReturnSource("admin"); setReturnDialogOpen(true); }}
              >
                <RotateCcw className="w-3.5 h-3.5" />
                החזר ליוצר להשלמה
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Distribution Summary */}
      {distributedProjects.length > 0 && (
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <FolderKanban className="w-5 h-5 text-accent" />
              סיכום הפצה
            </CardTitle>
          </CardHeader>
          <CardContent>
            {(() => {
              const total = lessonImpls.length || distributedProjects.length;
              const responded = lessonImpls.filter((i) => i.respondedBy);
              const implemented = responded.filter((i) => i.isRelevant && i.isImplemented).length;
              const relevantNotImpl = responded.filter((i) => i.isRelevant && !i.isImplemented).length;
              const notRelevant = responded.filter((i) => !i.isRelevant).length;
              const pending = Math.max(0, total - responded.length);
              return (
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-center">
                  <div className="p-3 rounded-xl bg-primary/5 border border-primary/10">
                    <p className="text-2xl font-bold text-primary">{total}</p>
                    <p className="text-xs text-muted-foreground mt-1">פרויקטים הופץ</p>
                  </div>
                  <div className="p-3 rounded-xl bg-success/5 border border-success/10">
                    <p className="text-2xl font-bold text-success">{implemented}</p>
                    <p className="text-xs text-muted-foreground mt-1">יושם</p>
                  </div>
                  <div className="p-3 rounded-xl bg-info/5 border border-info/10">
                    <p className="text-2xl font-bold text-info">{relevantNotImpl}</p>
                    <p className="text-xs text-muted-foreground mt-1">רלוונטי, טרם יושם</p>
                  </div>
                  <div className="p-3 rounded-xl bg-destructive/5 border border-destructive/10">
                    <p className="text-2xl font-bold text-destructive">{notRelevant}</p>
                    <p className="text-xs text-muted-foreground mt-1">לא רלוונטי</p>
                  </div>
                  <div className="p-3 rounded-xl bg-warning/5 border border-warning/10">
                    <p className="text-2xl font-bold text-warning">{pending}</p>
                    <p className="text-xs text-muted-foreground mt-1">ממתין לתגובה</p>
                  </div>
                </div>
              );
            })()}
          </CardContent>
        </Card>
      )}

      {/* AI Analysis */}
      {(lesson.aiReview || (currentUser.role === "admin" && lesson.status !== "new")) && (
        <Card className="border-0 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Brain className="w-5 h-5 text-accent" />
              ניתוח AI
            </CardTitle>
            {currentUser.role === "admin" && lessonImpls.some((i) => i.respondedBy) && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleReAnalyze}
                disabled={isReAnalyzing}
                className="gap-1.5"
              >
                {isReAnalyzing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                {isReAnalyzing ? "מנתח..." : "ניתוח מחדש"}
              </Button>
            )}
          </CardHeader>
          <CardContent className="space-y-4">
            {!lesson.aiReview && (
              <div className="text-center py-6 text-muted-foreground">
                <Brain className="w-8 h-8 mx-auto mb-2 opacity-30" />
                <p className="text-sm">לחץ על "ניתוח מחדש" כדי לנתח את הלקח בהתאם לפידבק</p>
              </div>
            )}
            {lesson.aiReview && (
              <>
                <p className="text-sm text-muted-foreground">{lesson.aiReview.summary}</p>

            {lesson.aiReview.implementation_steps.length > 0 && (
              <div className="space-y-1.5">
                <h4 className="text-sm font-semibold">🔧 צעדי יישום מומלצים:</h4>
                {lesson.aiReview.implementation_steps.map((step, i) => (
                  <p key={i} className="text-xs text-muted-foreground mr-4">{i + 1}. {step}</p>
                ))}
              </div>
            )}

            {lesson.aiReview.attention_points.length > 0 && (
              <div className="space-y-1.5">
                <h4 className="text-sm font-semibold flex items-center gap-1">
                  ⚠️ נקודות תשומת לב:
                </h4>
                {lesson.aiReview.attention_points.map((pt, i) => (
                  <p key={i} className="text-xs text-muted-foreground mr-4">⚠ {pt}</p>
                ))}
              </div>
            )}

            {lesson.aiReview.recommended_projects.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-sm font-semibold">📋 פרויקטים מומלצים:</h4>
                {lesson.aiReview.recommended_projects.map((rp, i) => (
                  <div key={i} className="p-2.5 rounded-lg bg-muted/50 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-sm">{rp.project_name}</span>
                      <Badge variant="outline" className="text-[10px]">עדיפות {rp.priority === "high" ? "גבוהה" : rp.priority === "medium" ? "בינונית" : "נמוכה"}</Badge>
                    </div>
                    <p className="text-muted-foreground mt-0.5">{rp.reason}</p>
                  </div>
                ))}
              </div>
            )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* Per-project implementation status */}
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <FolderKanban className="w-5 h-5 text-accent" />
            סטטוס בפרויקטים ({distributedProjects.length + (lesson.projectId ? 1 : 0)})
          </CardTitle>
          <p className="text-xs text-muted-foreground">סטטוס הלקח ופידבק מנהלי הפרויקטים בכל פרויקט רלוונטי</p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Source project */}
          <div className="p-4 rounded-xl bg-primary/5 border border-primary/10">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FolderKanban className="w-4 h-4 text-primary" />
                <span className="text-sm font-medium">{lesson.project}</span>
                <Badge variant="outline" className="text-[10px] bg-primary/10 text-primary border-primary/20">פרויקט מקור</Badge>
              </div>
            </div>
          </div>

          {/* Distributed projects with implementation status */}
          {distributedProjects.map((project) => {
            const impl = lessonImpls.find((i) => i.projectId === project.id);
            const pm = users.find((u) => u.id === project.managerId);
            const isMyProject = currentUser.role === "admin" || currentUser.assignedProjects.includes(project.id);

            return (
              <div key={project.id} className="p-4 rounded-xl bg-muted/50 border border-border space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FolderKanban className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm font-medium">{project.name}</span>
                    {pm && <span className="text-xs text-muted-foreground">(מנהל: {pm.name})</span>}
                  </div>
                  {impl?.respondedBy ? (
                    <Badge variant="outline" className={impl.isRelevant ? "bg-success/10 text-success border-success/20" : "bg-destructive/10 text-destructive border-destructive/20"}>
                      {impl.isRelevant ? (impl.isImplemented ? "יושם ✓" : "רלוונטי") : "לא רלוונטי"}
                    </Badge>
                  ) : impl ? (
                    <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20">ממתין לתגובה</Badge>
                  ) : (
                    <Badge variant="outline" className="bg-muted text-muted-foreground">טרם נותח</Badge>
                  )}
                </div>

                {/* Show feedback if responded */}
                {impl?.respondedBy && (
                  <div className="text-xs text-muted-foreground bg-background rounded-lg p-2.5">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium">{users.find((u) => u.id === impl.respondedBy)?.name}</span>
                      <span>·</span>
                      <span>{impl.respondedAt ? new Date(impl.respondedAt).toLocaleDateString("he-IL") : ""}</span>
                    </div>
                    {impl.reason && <p>סיבת הרלוונטיות: {impl.reason}</p>}
                    {impl.notes && <p className="mt-1">הערות: {impl.notes}</p>}
                  </div>
                )}

                {/* Allow PM to respond if pending */}
                {impl && !impl.respondedBy && isMyProject && (
                  <div className="space-y-2">
                    <Textarea
                      placeholder="הערות (אופציונלי)..."
                      value={feedbackNotes[impl.id] || ""}
                      onChange={(e) => setFeedbackNotes((prev) => ({ ...prev, [impl.id]: e.target.value }))}
                      rows={2}
                      className="text-sm"
                    />
                    <div className="flex gap-2 flex-wrap">
                      <Button size="sm" className="bg-success text-success-foreground hover:bg-success/90 gap-1" onClick={() => handleRespond(impl.id, true, true)}>
                        <ThumbsUp className="w-3.5 h-3.5" />
                        רלוונטי ויושם
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1 border-info text-info" onClick={() => handleRespond(impl.id, true, false)}>
                        <MessageSquare className="w-3.5 h-3.5" />
                        רלוונטי, לא יושם
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1 border-destructive text-destructive" onClick={() => handleRespond(impl.id, false, false)}>
                        <ThumbsDown className="w-3.5 h-3.5" />
                        לא רלוונטי
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {distributedProjects.length === 0 && addableProjects.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-4">הלקח טרם הופץ לפרויקטים נוספים</p>
          )}

          {/* PM: Add lesson to my project */}
          {addableProjects.length > 0 && (
            <div className="p-4 rounded-xl border-2 border-dashed border-primary/20 bg-primary/5 space-y-2">
              <p className="text-sm font-medium text-primary flex items-center gap-1.5">
                <PlusCircle className="w-4 h-4" />
                הוסף לקח זה לפרויקט שלך
              </p>
              <div className="flex flex-wrap gap-2">
                {addableProjects.map((project) => (
                  <Button
                    key={project.id}
                    size="sm"
                    variant="outline"
                    className="gap-1.5 border-primary/30 text-primary hover:bg-primary hover:text-primary-foreground"
                    disabled={isAddingToProject}
                    onClick={() => handleAddToProject(project.id)}
                  >
                    {isAddingToProject ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PlusCircle className="w-3.5 h-3.5" />}
                    {project.name}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {/* Referent self-add */}
          {canReferentSelfAdd && (
            <div className="p-4 rounded-xl border-2 border-dashed border-info/20 bg-info/5 space-y-2">
              <p className="text-sm font-medium text-info flex items-center gap-1.5">
                <PlusCircle className="w-4 h-4" />
                הוסף לקח זה לרשימת הלקחים שלך
              </p>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 border-info/30 text-info hover:bg-info hover:text-info-foreground"
                disabled={isAddingToProject}
                onClick={async () => {
                  setIsAddingToProject(true);
                  try {
                    const newReferentIds = [...(lesson.distributedToReferents || []), currentUser.id];
                    await supabase.from("lessons").update({
                      distributed_to_referents: newReferentIds,
                      status: "distributed",
                    }).eq("id", lesson.id);
                    await supabase.from("referent_reviews" as any).insert({
                      lesson_id: lesson.id,
                      referent_id: currentUser.id,
                      reason: "נוסף ידנית ע״י הרפרנט",
                      priority: "medium",
                    });
                    toast({ title: "הלקח נוסף לרשימת הלקחים שלך ✓" });
                    window.location.reload();
                  } catch (e) {
                    console.error("Error self-adding lesson:", e);
                    toast({ title: "שגיאה בהוספת הלקח", variant: "destructive" });
                  } finally {
                    setIsAddingToProject(false);
                  }
                }}
              >
                {isAddingToProject ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PlusCircle className="w-3.5 h-3.5" />}
                הוסף לרשימתי
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
      {(distributedReferents.length > 0 || (currentUser.role === "referent" && lessonRefReviews.some((r) => r.referentId === currentUser.id))) && (
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <BookOpen className="w-5 h-5 text-info" />
              סטטוס רפרנטים ({distributedReferents.length})
            </CardTitle>
            <p className="text-xs text-muted-foreground">פידבק מרפרנטים על הלקח</p>
          </CardHeader>
          <CardContent className="space-y-4">
            {distributedReferents.map((referent) => {
              const review = lessonRefReviews.find((r) => r.referentId === referent.id);
              const isMe = currentUser.id === referent.id || currentUser.role === "admin";
              const refStages = referent.assignedStageIndexes?.map((si) => PROJECT_STAGES[si]).filter(Boolean) || [];

              return (
                <div key={referent.id} className="p-4 rounded-xl bg-muted/50 border border-border space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <BookOpen className="w-4 h-4 text-muted-foreground" />
                      <span className="text-sm font-medium">{referent.name}</span>
                      <Badge variant="outline" className="text-[10px] bg-info/10 text-info border-info/20">רפרנט</Badge>
                      {refStages.length > 0 && (
                        <span className="text-xs text-muted-foreground">({refStages.join(", ")})</span>
                      )}
                    </div>
                    {review?.respondedAt ? (
                      <Badge variant="outline" className={review.isRelevant ? "bg-success/10 text-success border-success/20" : "bg-destructive/10 text-destructive border-destructive/20"}>
                        {review.isRelevant ? (review.isImplemented ? "יושם ✓" : "רלוונטי") : "לא רלוונטי"}
                      </Badge>
                    ) : review ? (
                      <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20">ממתין לתגובה</Badge>
                    ) : null}
                  </div>

                  {/* Show feedback if responded */}
                  {review?.respondedAt && (
                    <div className="text-xs text-muted-foreground bg-background rounded-lg p-2.5">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium">{referent.name}</span>
                        <span>·</span>
                        <span>{new Date(review.respondedAt).toLocaleDateString("he-IL")}</span>
                      </div>
                      {review.reason && <p>סיבת הרלוונטיות: {review.reason}</p>}
                      {review.notes && <p className="mt-1">הערות: {review.notes}</p>}
                    </div>
                  )}

                  {/* Allow referent to respond */}
                  {review && !review.respondedAt && currentUser.id === referent.id && (
                    <div className="space-y-2">
                      <Textarea
                        placeholder="הערות (אופציונלי)..."
                        value={feedbackNotes[`ref_${review.id}`] || ""}
                        onChange={(e) => setFeedbackNotes((prev) => ({ ...prev, [`ref_${review.id}`]: e.target.value }))}
                        rows={2}
                        className="text-sm"
                      />
                      <div className="flex gap-2 flex-wrap">
                        <Button size="sm" className="bg-success text-success-foreground hover:bg-success/90 gap-1" onClick={() => handleReferentRespond(review.id, true, true)}>
                          <ThumbsUp className="w-3.5 h-3.5" />
                          רלוונטי ויושם
                        </Button>
                        <Button size="sm" variant="outline" className="gap-1 border-info text-info" onClick={() => handleReferentRespond(review.id, true, false)}>
                          <MessageSquare className="w-3.5 h-3.5" />
                          רלוונטי, לא יושם
                        </Button>
                        <Button size="sm" variant="outline" className="gap-1 border-destructive text-destructive" onClick={() => handleReferentRespond(review.id, false, false)}>
                          <ThumbsDown className="w-3.5 h-3.5" />
                          לא רלוונטי
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {/* Return Lesson Dialog */}
      <ReturnLessonDialog
        open={returnDialogOpen}
        onOpenChange={setReturnDialogOpen}
        lessonTitle={lesson.title}
        onConfirm={async (reason, customReason) => {
          await returnLessonToCreator(lesson.id, reason, customReason);
          toast({ title: "הלקח הוחזר ליוצר להשלמה ✓" });
        }}
      />
    </div>
  );
};

export default LessonDetail;
