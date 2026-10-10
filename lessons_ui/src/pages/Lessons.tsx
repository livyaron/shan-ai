import { useState, useRef, useMemo, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { BookOpen, Search, Filter, Plus, CheckCircle, XCircle, Send, Wrench, ChevronLeft, Brain, Loader2, Star, AlertTriangle, Lightbulb, ThumbsUp, Trash2, Download, Mail, Lock, Archive, Eye, EyeOff, CalendarIcon, Upload, ArrowUpDown } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import SearchableSelect from "@/components/ui/searchable-select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useUser, PROJECT_STAGES, projectTypeLabels, stationTypeLabels } from "@/context/UserContext";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";
import { useFieldLabels } from "@/hooks/useFieldLabels";
import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import { riskColors, riskLabelsShort as riskLabels, statusColors, statusLabels, PROFESSIONAL_DOMAINS, WORKFLOW_STATUSES, ACTIVE_WORKFLOW_STATUSES, workflowStatusColors, calculateSlaStatus, slaStatusColors, SLA_STATUS } from "@/lib/constants";
import { useActionRequired } from "@/hooks/useActionRequired";
import { Zap } from "lucide-react";
import { format } from "date-fns";
import { cn } from "@/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import LessonsKanbanBoard from "@/components/LessonsKanbanBoard";

const stages = ["הקפאת תכולה", "הקפאת תצורה", "תכנון", "קבלת היתר", "בחירת קבלן", "עבודות אזרחיות", "הרכבות חשמליות", "בדיקות", "טופס 4", "חישמול"];
// categories are now loaded dynamically from context

interface PreSubmitReview {
  quality_score: number;
  is_duplicate: boolean;
  duplicate_of?: string;
  improvements: string[];
  strengths: string[];
  summary: string;
}

interface PostApproveReview {
  recommended_projects: { project_name: string; reason: string; priority: string }[];
  recommended_referents: { referent_id: string; referent_name: string; reason: string }[];
  implementation_steps: string[];
  attention_points: string[];
  summary: string;
}

const Lessons = () => {
  const { getLabel } = useFieldLabels("lesson");
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [riskFilter, setRiskFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [stageFilter, setStageFilter] = useState("all");
  const [projectFilter, setProjectFilter] = useState("all");
  const [creatorFilter, setCreatorFilter] = useState("all");
  const [equipmentFilter, setEquipmentFilter] = useState("all");
  const [monthFilter, setMonthFilter] = useState("all");
  const [stationTypeFilter, setStationTypeFilter] = useState("all");
  const [projectTypeFilter, setProjectTypeFilter] = useState("all");
  const [dateSortOrder, setDateSortOrder] = useState<"desc" | "asc">("desc");
  const [showAll, setShowAll] = useState(false);
  // Board filters
  const [boardSearch, setBoardSearch] = useState("");
  const [boardProjectFilter, setBoardProjectFilter] = useState("all");
  const [boardDomainFilter, setBoardDomainFilter] = useState("all");
  const [boardReferentFilter, setBoardReferentFilter] = useState("all");
  const [boardStatusFilter, setBoardStatusFilter] = useState("all");
  const [boardSlaFilter, setBoardSlaFilter] = useState("all");
  const [boardStationTypeFilter, setBoardStationTypeFilter] = useState("all");
  const [boardProjectTypeFilter, setBoardProjectTypeFilter] = useState("all");
  const [boardDateSort, setBoardDateSort] = useState<"desc" | "asc">("desc");
  const [activeTab, setActiveTab] = useState<"repository" | "board">("repository");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [returnToProjectId, setReturnToProjectId] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newRecommendation, setNewRecommendation] = useState("");
  const [newProject, setNewProject] = useState("");
  const [newPastProjectName, setNewPastProjectName] = useState("");
  const [newStage, setNewStage] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [newRisk, setNewRisk] = useState("");
  const [newEquipmentIds, setNewEquipmentIds] = useState<number[]>([]);
  const [newProfessionalDomain, setNewProfessionalDomain] = useState("");
  const [newEventDate, setNewEventDate] = useState<Date | undefined>(undefined);
  const [newAssignedTo, setNewAssignedTo] = useState("");
  const [newImpactScheduleDelay, setNewImpactScheduleDelay] = useState("");
  const [newImpactBudgetCost, setNewImpactBudgetCost] = useState("");
  const [newImpactQualityDesc, setNewImpactQualityDesc] = useState("");
  const [newFile, setNewFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // AI review states
  const [isReviewing, setIsReviewing] = useState(false);
  const [preReview, setPreReview] = useState<PreSubmitReview | null>(null);
  const [postReviewDialogOpen, setPostReviewDialogOpen] = useState(false);
  const [postReview, setPostReview] = useState<PostApproveReview | null>(null);
  const [isPostReviewing, setIsPostReviewing] = useState(false);
  const [reviewingLessonId, setReviewingLessonId] = useState<number | null>(null);
  const [selectedAIProjects, setSelectedAIProjects] = useState<number[]>([]);
  const [selectedAIReferents, setSelectedAIReferents] = useState<string[]>([]);
  const [filteredOutAIProjects, setFilteredOutAIProjects] = useState<string[]>([]);
  const [filteredOutAIReferents, setFilteredOutAIReferents] = useState<string[]>([]);

  // Manual distribution states
  const [manualDistDialogOpen, setManualDistDialogOpen] = useState(false);
  const [manualDistLessonId, setManualDistLessonId] = useState<number | null>(null);
  const [selectedDistProjects, setSelectedDistProjects] = useState<number[]>([]);
  const [selectedDistReferents, setSelectedDistReferents] = useState<string[]>([]);

  // Export state
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [exportFilterType, setExportFilterType] = useState<"all" | "project" | "category">("all");
  const [exportProjectId, setExportProjectId] = useState("");
  const [exportCategory, setExportCategory] = useState("");

  const { currentUser, hasPermission, lessons, addLesson, approveLesson, rejectLesson, closeLesson, deleteLesson, removeLessonFromProject, distributeLesson, updateLessonAIReview, users, projects, equipment, implementations, referentReviews, lessonCategories: categories } = useUser();
  const searchParamsKey = searchParams.toString();
  const routeLessonIdSet = useMemo(() => {
    const idsParam = new URLSearchParams(searchParamsKey).get("ids");
    if (!idsParam) return null;

    const parsedIds = idsParam
      .split(",")
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value));

    return parsedIds.length > 0 ? new Set(parsedIds) : null;
  }, [searchParamsKey]);

  // Auto-open lesson dialog from project detail navigation
  useEffect(() => {
    const newLessonProjectId = searchParams.get("newLesson");
    if (newLessonProjectId) {
      setNewProject(newLessonProjectId);
      setReturnToProjectId(newLessonProjectId);
      setDialogOpen(true);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // Apply drill-down query params from dashboard/KPIs
  useEffect(() => {
    const params = new URLSearchParams(searchParamsKey);
    const tabParam = params.get("tab") === "board" ? "board" : "repository";
    const statusParam = params.get("status");

    setActiveTab(tabParam);
    setCategoryFilter(params.get("category") || "all");
    setStageFilter(params.get("stage") || "all");
    setProjectFilter(params.get("project") || params.get("projectId") || "all");
    setRiskFilter(params.get("risk") || "all");
    setEquipmentFilter(params.get("equipmentId") || "all");
    setMonthFilter(params.get("month") || "all");

    if (tabParam === "board") {
      setBoardStatusFilter(statusParam || "all");
      setBoardSlaFilter(params.get("sla") || "all");
      setStatusFilter("all");
    } else {
      setStatusFilter(statusParam || "all");
      setBoardStatusFilter("all");
      setBoardSlaFilter("all");
    }
  }, [searchParamsKey]);

  const isAdmin = currentUser.role === "admin";
  const isReferent = currentUser.role === "referent";
  const isViewer = currentUser.role === "viewer";
  const canApprove = hasPermission("approve_lessons");
  const canDistribute = hasPermission("distribute_lessons");
  const { lessonNeedsAction } = useActionRequired(lessons, implementations, projects, currentUser, referentReviews);

  // Project IDs the current user actively manages (managerId only — NOT assignedProjects)
  const myManagedProjectIds = useMemo(
    () => projects.filter((p) => p.managerId === currentUser.id).map((p) => p.id),
    [projects, currentUser.id]
  );

  const distributedToMyProjects = !isAdmin && !isReferent && !isViewer
    ? lessons.filter((l) =>
        l.distributedTo?.some((pid) => myManagedProjectIds.includes(pid))
      ).map((l) => l.id)
    : [];

  const distributedToMe = isReferent
    ? lessons.filter((l) => l.distributedToReferents?.includes(currentUser.id)).map((l) => l.id)
    : [];

  // Default scope (when showAll is OFF):
  // - admin / viewer: full org-wide read access
  // - referent: only lessons distributed to them
  // - project_manager: only lessons they created
  const myScopeLessons = isAdmin || isViewer
    ? lessons
    : isReferent
    ? lessons.filter((l) => distributedToMe.includes(l.id) || l.createdBy === currentUser.id)
    : lessons.filter((l) => l.createdBy === currentUser.id);

  // viewer never sees the showAll toggle — they always have full access.
  const userLessons = (showAll && !isViewer) ? lessons : myScopeLessons;

  const getLessonOrigin = (lesson: typeof lessons[0]) => {
    if (lesson.createdBy === currentUser.id) return "created";
    if (lesson.projectId !== null && myManagedProjectIds.includes(lesson.projectId)) return "my_project";
    if (distributedToMyProjects.includes(lesson.id)) return "distributed";
    return "other";
  };

  const filtered = userLessons.filter((l) => {
    const matchIds = !routeLessonIdSet || routeLessonIdSet.has(l.id);
    const matchSearch = l.title.includes(search) || l.project.includes(search);
    const matchRisk = riskFilter === "all" || l.risk === riskFilter;
    const matchStatus = statusFilter === "all" || l.status === statusFilter;
    const matchCategory = categoryFilter === "all" || l.category === categoryFilter;
    const matchStage = stageFilter === "all" || l.stage === stageFilter;
    const matchProject = projectFilter === "all" || l.projectId === Number(projectFilter);
    const matchCreator = creatorFilter === "all" || l.createdBy === creatorFilter;
    const matchEquipment = equipmentFilter === "all" || l.equipmentIds.includes(Number(equipmentFilter));
    const matchMonth = monthFilter === "all" || (() => {
      const lessonDate = new Date(l.date);
      if (isNaN(lessonDate.getTime())) return false;
      const lessonMonth = `${lessonDate.getFullYear()}-${String(lessonDate.getMonth() + 1).padStart(2, "0")}`;
      return lessonMonth === monthFilter;
    })();
    const linkedProject = l.projectId != null ? projects.find((p) => p.id === l.projectId) : null;
    const matchStationType = stationTypeFilter === "all" || (linkedProject && linkedProject.stationType === stationTypeFilter);
    const matchProjectType = projectTypeFilter === "all" || (linkedProject && linkedProject.projectType === projectTypeFilter);
    return matchIds && matchSearch && matchRisk && matchStatus && matchCategory && matchStage && matchProject && matchCreator && matchEquipment && matchMonth && matchStationType && matchProjectType;
  }).sort((a, b) => {
    // Primary sort: action required
    const aAction = lessonNeedsAction(a.id).needed ? 1 : 0;
    const bAction = lessonNeedsAction(b.id).needed ? 1 : 0;
    if (bAction !== aAction) return bAction - aAction;
    // Secondary sort: date
    const aDate = new Date(a.date).getTime();
    const bDate = new Date(b.date).getTime();
    return dateSortOrder === "desc" ? bDate - aDate : aDate - bDate;
  });

  // Board lessons - filtered by role and active workflow statuses
  const boardLessons = useMemo(() => {
    let bl = userLessons.filter((l) => ACTIVE_WORKFLOW_STATUSES.includes(l.workflowStatus));
    if (routeLessonIdSet) bl = bl.filter((l) => routeLessonIdSet.has(l.id));
    if (boardSearch) bl = bl.filter((l) => l.title.includes(boardSearch) || l.project.includes(boardSearch));
    if (boardProjectFilter !== "all") bl = bl.filter((l) => l.projectId === Number(boardProjectFilter));
    if (boardDomainFilter !== "all") bl = bl.filter((l) => l.professionalDomain === boardDomainFilter);
    if (boardReferentFilter !== "all") bl = bl.filter((l) => l.assignedTo === boardReferentFilter);
    if (boardStatusFilter !== "all") bl = bl.filter((l) => l.workflowStatus === boardStatusFilter);
    if (boardSlaFilter !== "all") {
      bl = bl.filter((l) => {
        const sla = calculateSlaStatus(l.referentStartDate);
        return sla.status === boardSlaFilter;
      });
    }
    if (boardStationTypeFilter !== "all") {
      bl = bl.filter((l) => {
        const lp = l.projectId != null ? projects.find((p) => p.id === l.projectId) : null;
        return lp && lp.stationType === boardStationTypeFilter;
      });
    }
    if (boardProjectTypeFilter !== "all") {
      bl = bl.filter((l) => {
        const lp = l.projectId != null ? projects.find((p) => p.id === l.projectId) : null;
        return lp && lp.projectType === boardProjectTypeFilter;
      });
    }
    // Sort by date
    bl.sort((a, b) => {
      const aDate = new Date(a.date).getTime();
      const bDate = new Date(b.date).getTime();
      return boardDateSort === "desc" ? bDate - aDate : aDate - bDate;
    });
    return bl;
  }, [userLessons, routeLessonIdSet, boardSearch, boardProjectFilter, boardDomainFilter, boardReferentFilter, boardStatusFilter, boardSlaFilter, boardStationTypeFilter, boardProjectTypeFilter, boardDateSort, projects]);

  const hasActiveFilters = riskFilter !== "all" || statusFilter !== "all" || categoryFilter !== "all" || stageFilter !== "all" || projectFilter !== "all" || creatorFilter !== "all" || equipmentFilter !== "all" || monthFilter !== "all" || stationTypeFilter !== "all" || projectTypeFilter !== "all";
  const reviewingLesson = reviewingLessonId ? lessons.find((lesson) => lesson.id === reviewingLessonId) : null;
  const reviewingLessonStageIndex = reviewingLesson ? PROJECT_STAGES.indexOf(reviewingLesson.stage) : -1;
  const autoDistributionTargetCount = selectedAIProjects.length + selectedAIReferents.length;

  const clearFilters = () => {
    setRiskFilter("all");
    setStatusFilter("all");
    setCategoryFilter("all");
    setStageFilter("all");
    setProjectFilter("all");
    setCreatorFilter("all");
    setEquipmentFilter("all");
    setMonthFilter("all");
    setStationTypeFilter("all");
    setProjectTypeFilter("all");
  };

  const closePostReviewDialog = () => {
    setPostReviewDialogOpen(false);
    setReviewingLessonId(null);
    setSelectedAIProjects([]);
    setSelectedAIReferents([]);
    setFilteredOutAIProjects([]);
    setFilteredOutAIReferents([]);
  };

  const userProjects = (currentUser.role === "admin" || currentUser.role === "referent")
    ? projects
    : projects.filter((p) => p.managerId === currentUser.id);

  // Pre-submit AI review
  const handleAIReview = async () => {
    const isPastProject = newProject === "__past__";
    const proj = isPastProject ? null : projects.find((p) => p.id === Number(newProject));
    const projectName = isPastProject ? newPastProjectName.trim() : proj?.name;
    if (!newTitle || !projectName || !newStage || !newCategory || !newRisk) {
      toast({ title: "שגיאה", description: "נא למלא את כל השדות לפני בדיקת AI", variant: "destructive" });
      return;
    }

    setIsReviewing(true);
    setPreReview(null);
    try {
      const { data, error } = await supabase.functions.invoke("review-lesson", {
        body: {
          type: "pre_submit",
          lesson: {
            title: newTitle,
            description: newDesc,
            projectName: projectName,
            stage: newStage,
            category: newCategory,
            risk: newRisk,
            equipmentIds: newEquipmentIds,
          },
          existingLessons: lessons.map((l) => ({ title: l.title, category: l.category, stage: l.stage })),
          equipment,
        },
      });
      if (error) throw error;
      setPreReview(data?.review || null);
    } catch (e) {
      console.error("AI review error:", e);
      toast({ title: "שגיאה בבדיקת AI", description: "לא ניתן היה לבצע בדיקה כעת", variant: "destructive" });
    } finally {
      setIsReviewing(false);
    }
  };

  const handleCreateLesson = async () => {
    const isPastProject = newProject === "__past__";
    const proj = isPastProject ? null : projects.find((p) => p.id === Number(newProject));
    const projectName = isPastProject ? newPastProjectName.trim() : proj?.name;
    
    if (!newTitle || !projectName || !newStage || !newCategory || !newRisk) {
      toast({ title: "שגיאה", description: "נא למלא את כל השדות", variant: "destructive" });
      return;
    }
    if (isPastProject && !newPastProjectName.trim()) {
      toast({ title: "שגיאה", description: "נא להזין שם פרויקט היסטורי", variant: "destructive" });
      return;
    }

    let fileUrl: string | undefined;
    if (newFile) {
      setIsUploading(true);
      try {
        const fileExt = newFile.name.split('.').pop();
        const filePath = `${Date.now()}_${Math.random().toString(36).slice(2)}.${fileExt}`;
        const { error: uploadError } = await supabase.storage.from('lesson-files').upload(filePath, newFile);
        if (uploadError) throw uploadError;
        const { data: urlData } = supabase.storage.from('lesson-files').getPublicUrl(filePath);
        fileUrl = urlData.publicUrl;
      } catch (e) {
        console.error("File upload error:", e);
        toast({ title: "שגיאה בהעלאת קובץ", variant: "destructive" });
        setIsUploading(false);
        return;
      }
      setIsUploading(false);
    }

    addLesson({
      title: newTitle,
      description: newDesc,
      recommendation: newRecommendation,
      project: projectName,
      projectId: proj?.id ?? null,
      stage: newStage,
      category: newCategory,
      risk: newRisk as "high" | "medium" | "low",
      equipmentIds: newEquipmentIds,
      professionalDomain: newProfessionalDomain || undefined,
      eventDate: newEventDate ? format(newEventDate, "yyyy-MM-dd") : undefined,
      assignedTo: newAssignedTo || undefined,
      impactScheduleDelay: newImpactScheduleDelay ? Number(newImpactScheduleDelay) : undefined,
      impactBudgetCost: newImpactBudgetCost ? Number(newImpactBudgetCost) : undefined,
      impactQualityDesc: newImpactQualityDesc || undefined,
      fileUrl,
    });
    toast({ title: "לקח נוצר בהצלחה ✓", description: "הלקח נשלח לאישור מנהל המערכת" });
    setDialogOpen(false);
    setNewTitle(""); setNewDesc(""); setNewRecommendation(""); setNewProject(""); setNewPastProjectName(""); setNewStage(""); setNewCategory(""); setNewRisk(""); setNewEquipmentIds([]);
    setNewProfessionalDomain(""); setNewEventDate(undefined); setNewAssignedTo("");
    setNewImpactScheduleDelay(""); setNewImpactBudgetCost(""); setNewImpactQualityDesc("");
    setNewFile(null);
    setPreReview(null);
    if (returnToProjectId) {
      navigate(`/projects/${returnToProjectId}`);
      setReturnToProjectId(null);
    }
  };

  // Post-approve AI review — auto-distributes after analysis
  const handleApprove = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    approveLesson(id);

    // Trigger post-approve AI review
    setReviewingLessonId(id);
    setIsPostReviewing(true);
    setPostReview(null);
    setPostReviewDialogOpen(true);

    try {
      const lesson = lessons.find((l) => l.id === id);
      if (!lesson) return;

      const lessonEquipment = equipment.filter((eq) => lesson.equipmentIds.includes(eq.id));
      const lessonStageIndex = PROJECT_STAGES.indexOf(lesson.stage);
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
      const referents = users
        .filter((user) => user.role === "referent")
        .map((user) => ({
          id: user.id,
          name: user.name,
          stages: user.assignedStageIndexes.map((stageIndex) => PROJECT_STAGES[stageIndex]).filter(Boolean),
        }));

      const { data, error } = await supabase.functions.invoke("review-lesson", {
        body: {
          type: "post_approve",
          lesson: {
            title: lesson.title,
            description: lesson.description,
            projectName: lesson.project,
            stage: lesson.stage,
            category: lesson.category,
            risk: lesson.risk,
            equipmentNames: lessonEquipment.map((eq) => eq.name),
          },
          projects: activeProjects,
          referents,
        },
      });
      if (error) throw error;
      const review = data?.review as PostApproveReview | null;
      setPostReview(review);

      if (review) {
        updateLessonAIReview(id, review);

        const validProjectIds: number[] = [];
        const invalidProjectNames: string[] = [];

        review.recommended_projects.forEach((recommendedProject) => {
          const foundProject = projects.find((project) => project.name === recommendedProject.project_name);
          if (foundProject && foundProject.stageIndex === lessonStageIndex) {
            validProjectIds.push(foundProject.id);
          } else {
            invalidProjectNames.push(recommendedProject.project_name);
          }
        });

        const validReferentIds: string[] = [];
        const invalidReferentNames: string[] = [];

        review.recommended_referents.forEach((recommendedReferent) => {
          const foundReferent = users.find((user) => user.id === recommendedReferent.referent_id && user.role === "referent");
          if (foundReferent && foundReferent.assignedStageIndexes.includes(lessonStageIndex)) {
            validReferentIds.push(foundReferent.id);
          } else {
            invalidReferentNames.push(recommendedReferent.referent_name);
          }
        });

        setSelectedAIProjects(validProjectIds);
        setSelectedAIReferents(validReferentIds);
        setFilteredOutAIProjects(invalidProjectNames);
        setFilteredOutAIReferents(invalidReferentNames);
      } else {
        setSelectedAIProjects([]);
        setSelectedAIReferents([]);
        setFilteredOutAIProjects([]);
        setFilteredOutAIReferents([]);
      }
    } catch (e) {
      console.error("Post-approve AI error:", e);
      toast({ title: "הלקח אושר", description: "אושר בהצלחה אך ניתוח AI נכשל — ניתן להפיץ ידנית", variant: "destructive" });
      closePostReviewDialog();
    } finally {
      setIsPostReviewing(false);
    }
  };

  const handleReject = (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    rejectLesson(id);
    toast({ title: "לקח נדחה", variant: "destructive" });
  };

  // Manual distribution
  const openManualDistribute = (e: React.MouseEvent, lessonId: number) => {
    e.stopPropagation();
    const lesson = lessons.find((l) => l.id === lessonId);
    if (!lesson) return;
    // Pre-select already distributed projects
    setSelectedDistProjects(lesson.distributedTo || []);
    // Pre-select referents by stage match
    const lessonStageIndex = PROJECT_STAGES.indexOf(lesson.stage);
    const matchingReferents = users.filter(
      (u) => u.role === "referent" && u.assignedStageIndexes?.includes(lessonStageIndex)
    );
    setSelectedDistReferents(lesson.distributedToReferents || matchingReferents.map((r) => r.id));
    setManualDistLessonId(lessonId);
    setManualDistDialogOpen(true);
  };

  const handleManualDistribute = () => {
    if (manualDistLessonId && (selectedDistProjects.length > 0 || selectedDistReferents.length > 0)) {
      distributeLesson(manualDistLessonId, selectedDistProjects, selectedDistReferents);
      const parts = [];
      if (selectedDistProjects.length > 0) parts.push(`${selectedDistProjects.length} פרויקטים`);
      if (selectedDistReferents.length > 0) parts.push(`${selectedDistReferents.length} רפרנטים`);
      toast({
        title: "לקח הופץ בהצלחה 🚀",
        description: `הופץ ל-${parts.join(" ו-")}`,
      });
      setManualDistDialogOpen(false);
    }
  };

  const toggleDistProject = (pid: number) => {
    setSelectedDistProjects((prev) =>
      prev.includes(pid) ? prev.filter((id) => id !== pid) : [...prev, pid]
    );
  };

  const toggleDistReferent = (rid: string) => {
    setSelectedDistReferents((prev) =>
      prev.includes(rid) ? prev.filter((id) => id !== rid) : [...prev, rid]
    );
  };

  const handleExport = () => {
    let exportLessons = userLessons;
    let fileName = "דוח_לקחים";

    if (exportFilterType === "project" && exportProjectId) {
      const pid = Number(exportProjectId);
      exportLessons = exportLessons.filter((l) => l.projectId === pid);
      const projName = projects.find((p) => p.id === pid)?.name || "";
      fileName = `דוח_לקחים_${projName}`;
    } else if (exportFilterType === "category" && exportCategory) {
      exportLessons = exportLessons.filter((l) => l.category === exportCategory);
      fileName = `דוח_לקחים_${exportCategory}`;
    }

    if (exportLessons.length === 0) {
      toast({ title: "אין לקחים לייצוא", description: "לא נמצאו לקחים בהתאם לסינון שנבחר", variant: "destructive" });
      return;
    }

    const statusLabelsMap: Record<string, string> = { new: "ממתין", approved: "מאושר", rejected: "נדחה", distributed: "הופץ" };
    const riskLabelsMap: Record<string, string> = { high: "גבוה", medium: "בינוני", low: "נמוך" };

    // BOM for Hebrew support in Excel
    const BOM = "\uFEFF";
    const headers = ["מזהה", "כותרת", "תיאור", "פרויקט", "שלב", "קטגוריה", "סיכון", "סטטוס", "תאריך", "נוצר ע\"י"];
    const rows = exportLessons.map((l) => [
      l.id,
      `"${l.title.replace(/"/g, '""')}"`,
      `"${l.description.replace(/"/g, '""')}"`,
      `"${l.project}"`,
      `"${l.stage}"`,
      `"${l.category}"`,
      riskLabelsMap[l.risk] || l.risk,
      statusLabelsMap[l.status] || l.status,
      l.date,
      `"${getCreatorName(l.createdBy)}"`,
    ].join(","));

    const csv = BOM + [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${fileName}.csv`;
    a.click();
    URL.revokeObjectURL(url);

    toast({ title: `דוח יוצא בהצלחה ✓`, description: `${exportLessons.length} לקחים יוצאו לקובץ CSV` });
    setExportDialogOpen(false);
  };

  const getFilteredExportLessons = () => {
    let exportLessons = userLessons;
    let filterLabel = "כל הלקחים";

    if (exportFilterType === "project" && exportProjectId) {
      const pid = Number(exportProjectId);
      exportLessons = exportLessons.filter((l) => l.projectId === pid);
      filterLabel = `פרויקט: ${projects.find((p) => p.id === pid)?.name || ""}`;
    } else if (exportFilterType === "category" && exportCategory) {
      exportLessons = exportLessons.filter((l) => l.category === exportCategory);
      filterLabel = `קטגוריה: ${exportCategory}`;
    }
    return { exportLessons, filterLabel };
  };

  const [isSendingEmail, setIsSendingEmail] = useState(false);

  const handleExportPDFEmail = async () => {
    const { exportLessons, filterLabel } = getFilteredExportLessons();

    if (exportLessons.length === 0) {
      toast({ title: "אין לקחים לייצוא", description: "לא נמצאו לקחים בהתאם לסינון שנבחר", variant: "destructive" });
      return;
    }

    setIsSendingEmail(true);

    const toBase64 = (buffer: ArrayBuffer) => {
      const bytes = new Uint8Array(buffer);
      const chunkSize = 0x8000;
      let binary = "";
      for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
      }
      return btoa(binary);
    };

    const statusLabelsMap: Record<string, string> = { new: "ממתין", approved: "מאושר", rejected: "נדחה", distributed: "הופץ" };
    const riskLabelsMap: Record<string, string> = { high: "גבוה", medium: "בינוני", low: "נמוך" };

    const fileName = exportFilterType === "project" && exportProjectId
      ? `lessons_report_project_${exportProjectId}`
      : exportFilterType === "category" && exportCategory
        ? `lessons_report_${exportCategory}`
        : "lessons_report";

    try {
      const doc = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });

      try {
        const fontResponse = await fetch(`${import.meta.env.BASE_URL}fonts/Rubik-Regular.ttf`);
        const fontBuffer = await fontResponse.arrayBuffer();
        const fontBase64 = toBase64(fontBuffer);
        doc.addFileToVFS("Rubik-Regular.ttf", fontBase64);
        doc.addFont("Rubik-Regular.ttf", "Rubik", "normal");
        doc.setFont("Rubik");
      } catch (e) {
        console.warn("Could not load Hebrew font, falling back to default", e);
      }

      const pageW = doc.internal.pageSize.getWidth();
      const reverseText = (text: string) => text.split("").reverse().join("");

      doc.setFontSize(18);
      doc.text(reverseText("דוח לקחים"), pageW / 2, 18, { align: "center" });
      doc.setFontSize(11);
      doc.text(reverseText(`${filterLabel} | סה"כ: ${exportLessons.length}`), pageW / 2, 26, { align: "center" });
      doc.text(reverseText(`תאריך: ${new Date().toLocaleDateString("he-IL")}`), pageW / 2, 32, { align: "center" });

      const headers = [["#", reverseText("כותרת"), reverseText("פרויקט"), reverseText("שלב"), reverseText("קטגוריה"), reverseText("סיכון"), reverseText("סטטוס"), reverseText("תאריך"), reverseText("נוצר ע״י")]];
      const rows = exportLessons.map((l, i) => [
        i + 1,
        reverseText(l.title),
        reverseText(l.project),
        reverseText(l.stage),
        reverseText(l.category),
        reverseText(riskLabelsMap[l.risk] || l.risk),
        reverseText(statusLabelsMap[l.status] || l.status),
        l.date,
        reverseText(getCreatorName(l.createdBy)),
      ]);

      autoTable(doc, {
        head: headers,
        body: rows,
        startY: 38,
        theme: "grid",
        styles: { fontSize: 8, cellPadding: 2, halign: "center", font: "Rubik" },
        headStyles: { fillColor: [59, 130, 246], textColor: 255, fontStyle: "normal", font: "Rubik" },
        alternateRowStyles: { fillColor: [245, 247, 250] },
        columnStyles: {
          0: { cellWidth: 10 },
          1: { cellWidth: 50, halign: "right" },
          2: { cellWidth: 30 },
          3: { cellWidth: 30 },
          4: { cellWidth: 25 },
          5: { cellWidth: 20 },
          6: { cellWidth: 20 },
          7: { cellWidth: 25 },
          8: { cellWidth: 30 },
        },
      });

      const pdfBase64 = toBase64(doc.output("arraybuffer"));

      const invokePromise = supabase.functions.invoke("send-notification-email", {
        body: {
          to: currentUser.email,
          subject: `דוח לקחים - ${filterLabel}`,
          body: `<p>שלום ${currentUser.name},</p><p>מצורף דוח לקחים שהפקת מהמערכת.</p><p><strong>סינון:</strong> ${filterLabel}</p><p><strong>מספר לקחים:</strong> ${exportLessons.length}</p>`,
          attachments: [{
            filename: `${fileName}.pdf`,
            content: pdfBase64,
            content_type: "application/pdf",
          }],
        },
      });

      const timeoutPromise = new Promise<never>((_, reject) => {
        setTimeout(() => reject(new Error("Email send timeout")), 20000);
      });

      const { error } = await Promise.race([invokePromise, timeoutPromise]);
      if (error) throw error;

      toast({ title: "דוח נשלח בהצלחה ✓", description: `הדוח נשלח למייל ${currentUser.email}` });
      setExportDialogOpen(false);
    } catch (e) {
      console.error("Email send error:", e);
      toast({
        title: "שליחה התעכבה",
        description: "שליחת המייל נכשלה או התעכבה. הקובץ ירד למחשב במקום זה.",
        variant: "destructive",
      });

      try {
        const fallbackDoc = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });
        fallbackDoc.text("Lessons Report", 14, 20);
        fallbackDoc.save(`${fileName}.pdf`);
      } catch {
        // no-op fallback
      }
    } finally {
      setIsSendingEmail(false);
    }
  };

  const getCreatorName = (id: string) => users.find((u) => u.id === id)?.name || id;

  const getImplCount = (lessonId: number) => implementations.filter((i) => i.lessonId === lessonId).length;
  const getRespondedCount = (lessonId: number) => implementations.filter((i) => i.lessonId === lessonId && i.respondedBy).length;

  const toggleNewEquipment = (eqId: number) => {
    setNewEquipmentIds((prev) => prev.includes(eqId) ? prev.filter((id) => id !== eqId) : [...prev, eqId]);
  };

  const scoreColor = (score: number) => {
    if (score >= 8) return "text-success";
    if (score >= 5) return "text-warning";
    return "text-destructive";
  };

  const priorityLabel: Record<string, string> = { high: "גבוהה", medium: "בינונית", low: "נמוכה" };
  const priorityColor: Record<string, string> = {
    high: "bg-destructive/10 text-destructive border-destructive/20",
    medium: "bg-warning/10 text-warning border-warning/20",
    low: "bg-success/10 text-success border-success/20",
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-heading font-bold">לקחים</h1>
        </div>
        <div className="flex gap-2 flex-wrap">
          {!isAdmin && (
            <Button
              variant={showAll ? "default" : "outline"}
              size="sm"
              onClick={() => setShowAll(!showAll)}
              className="gap-1.5"
            >
              {showAll ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              {showAll ? "הלקחים שלי" : "כל הלקחים"}
            </Button>
          )}
          <Button variant="outline" className="gap-2" onClick={() => setExportDialogOpen(true)}>
            <Download className="w-4 h-4" />
            ייצוא דוח
          </Button>
          {hasPermission("add_lessons") && (
          <Dialog open={dialogOpen} onOpenChange={(open) => { setDialogOpen(open); if (!open) { setPreReview(null); if (returnToProjectId) { navigate(`/projects/${returnToProjectId}`); setReturnToProjectId(null); } } }}>
            <DialogTrigger asChild>
              <Button className="bg-accent text-accent-foreground hover:bg-accent/90 gap-2">
                <Plus className="w-4 h-4" />
                לקח חדש
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto" dir="rtl">
              <DialogHeader>
                 <DialogTitle className="font-heading">יצירת לקח חדש</DialogTitle>
                 <DialogDescription>מלא את הפרטים כדי ליצור לקח חדש</DialogDescription>
              </DialogHeader>
               <div className="space-y-5 mt-2">
                {/* === אזור 1 – פרטי הלקח === */}
                <div className="space-y-3">
                  <h4 className="text-sm font-semibold text-primary border-b border-border pb-1">פרטי הלקח</h4>
                  <div className="space-y-2">
                    <Label>{getLabel("title", "כותרת הלקח")}</Label>
                    <Input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="תאר את הלקח בקצרה..." />
                  </div>
                  <div className="space-y-2">
                    <Label>{getLabel("description", "תיאור מפורט")}</Label>
                    <Textarea value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder={"תאר מה קרה בפועל.\nמה הייתה הבעיה או האירוע?\nמה גרם לכך?\nכיצד הדבר השפיע על הפרויקט?"} rows={4} />
                  </div>
                  <div className="space-y-2">
                    <Label>{getLabel("recommendation", "המלצה")}</Label>
                    <Textarea value={newRecommendation} onChange={(e) => setNewRecommendation(e.target.value)} placeholder="מה ההמלצה שלך? מה צריך לעשות אחרת בפעם הבאה..." rows={2} />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <Label>{getLabel("project_name", "פרויקט מקור")}</Label>
                      <SearchableSelect value={newProject} onValueChange={(val) => { setNewProject(val); if (val !== "__past__") setNewPastProjectName(""); }} placeholder="בחר פרויקט"
                        options={[
                          ...userProjects.map((p) => ({ value: String(p.id), label: p.name })),
                          { value: "__past__", label: "📁 פרויקט היסטורי" },
                        ]}
                      />
                      {newProject === "__past__" && (
                        <Input
                          value={newPastProjectName}
                          onChange={(e) => setNewPastProjectName(e.target.value)}
                          placeholder="הזן שם פרויקט היסטורי..."
                          className="mt-1"
                        />
                      )}
                    </div>
                    <div className="space-y-2">
                      <Label>{getLabel("stage", "שלב")}</Label>
                      <SearchableSelect value={newStage} onValueChange={setNewStage} placeholder="בחר שלב"
                        options={[
                          ...stages.map((s) => ({ value: String(s), label: s })),
                        ]}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>{getLabel("category", "קטגוריה")}</Label>
                      <SearchableSelect value={newCategory} onValueChange={setNewCategory} placeholder="בחר קטגוריה"
                        options={[
                          ...categories.map((c) => ({ value: String(c), label: c })),
                        ]}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>{getLabel("risk", "רמת סיכון")}</Label>
                      <SearchableSelect value={newRisk} onValueChange={setNewRisk} placeholder="בחר רמה"
                        options={[
                          { value: "high", label: "גבוה" },
                          { value: "medium", label: "בינוני" },
                          { value: "low", label: "נמוך" },
                        ]}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>{getLabel("professional_domain", "תחום מקצועי / מגזר")}</Label>
                      <SearchableSelect value={newProfessionalDomain} onValueChange={setNewProfessionalDomain} placeholder="בחר תחום"
                        options={[
                          ...PROFESSIONAL_DOMAINS.map((d) => ({ value: String(d), label: d })),
                        ]}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>{getLabel("event_date", "תאריך האירוע")}</Label>
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button
                            variant="outline"
                            className={cn(
                              "w-full justify-start text-right font-normal",
                              !newEventDate && "text-muted-foreground"
                            )}
                          >
                            <CalendarIcon className="ml-2 h-4 w-4" />
                            {newEventDate ? format(newEventDate, "dd/MM/yyyy") : "בחר תאריך"}
                          </Button>
                        </PopoverTrigger>
                        <PopoverContent className="w-auto p-0" align="start">
                          <Calendar
                            mode="single"
                            selected={newEventDate}
                            onSelect={setNewEventDate}
                            initialFocus
                            className={cn("p-3 pointer-events-auto")}
                          />
                        </PopoverContent>
                      </Popover>
                    </div>
                  </div>
                </div>

                {/* === אזור 2 – השפעה === */}
                <div className="space-y-3">
                  <h4 className="text-sm font-semibold text-primary border-b border-border pb-1">השפעה</h4>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <Label>{getLabel("impact_schedule_delay", "השפעה על לו״ז – הערכת עיכוב (ימים)")}</Label>
                      <Input type="number" value={newImpactScheduleDelay} onChange={(e) => setNewImpactScheduleDelay(e.target.value)} placeholder="מספר ימים" />
                    </div>
                    <div className="space-y-2">
                      <Label>{getLabel("impact_budget_cost", "השפעה על תקציב – הערכת עלות (₪)")}</Label>
                      <Input type="number" value={newImpactBudgetCost} onChange={(e) => setNewImpactBudgetCost(e.target.value)} placeholder="סכום ב-₪" />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label>{getLabel("impact_quality_desc", "השפעה על איכות – תיאור השפעה")}</Label>
                    <Textarea value={newImpactQualityDesc} onChange={(e) => setNewImpactQualityDesc(e.target.value)} placeholder="תאר את ההשפעה על האיכות..." rows={2} />
                  </div>
                </div>

                {/* === צירוף קובץ === */}
                <div className="space-y-2">
                  <Label>{getLabel("file_url", "צרף קובץ / תמונה")}</Label>
                  <div className="flex items-center gap-2">
                    <input
                      type="file"
                      ref={fileInputRef}
                      className="hidden"
                      accept="image/*,.pdf,.doc,.docx,.xls,.xlsx"
                      onChange={(e) => setNewFile(e.target.files?.[0] || null)}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="gap-1.5"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <Upload className="w-4 h-4" />
                      {newFile ? newFile.name : "בחר קובץ"}
                    </Button>
                    {newFile && (
                      <Button type="button" variant="ghost" size="sm" onClick={() => { setNewFile(null); if (fileInputRef.current) fileInputRef.current.value = ""; }}>
                        ✕
                      </Button>
                    )}
                  </div>
                </div>

                {/* === אזור 3 – ציוד / נושאים === */}
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold text-primary border-b border-border pb-1 flex items-center gap-1.5">
                    <Wrench className="w-3.5 h-3.5" />
                    ציוד / נושאים קשורים
                  </h4>
                  <div className="grid grid-cols-2 gap-1.5 max-h-32 overflow-y-auto">
                    {equipment.map((eq) => (
                      <label key={eq.id} className="flex items-center gap-2 text-sm cursor-pointer p-1.5 rounded hover:bg-muted/50">
                        <Checkbox
                          checked={newEquipmentIds.includes(eq.id)}
                          onCheckedChange={() => toggleNewEquipment(eq.id)}
                        />
                        {eq.name}
                      </label>
                    ))}
                  </div>
                </div>

                {/* === אזור 4 – טיפול ומעקב === */}
                <div className="space-y-3">
                  <h4 className="text-sm font-semibold text-primary border-b border-border pb-1">טיפול ומעקב</h4>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <Label>{getLabel("assigned_to", "אחראי ליישום")}</Label>
                      <SearchableSelect value={newAssignedTo} onValueChange={setNewAssignedTo} placeholder="בחר משתמש"
                        options={[
                          ...users.map((u) => ({ value: String(u.id), label: u.name })),
                        ]}
                      />
                    </div>
                  </div>
                </div>

                {/* AI Review Button */}
                <Button
                  variant="outline"
                  onClick={handleAIReview}
                  disabled={isReviewing}
                  className="w-full gap-2 border-accent/30 text-accent hover:bg-accent/10"
                >
                  {isReviewing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Brain className="w-4 h-4" />}
                  {isReviewing ? "AI בודק..." : "🔍 בדיקת AI לפני שליחה"}
                </Button>

                {/* Pre-submit AI Review Results */}
                {preReview && (
                  <div className="rounded-xl border border-accent/20 bg-accent/5 p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold flex items-center gap-1.5">
                        <Brain className="w-4 h-4 text-accent" />
                        ממצאי AI
                      </h4>
                      <div className="flex items-center gap-1.5">
                        <Star className={`w-4 h-4 ${scoreColor(preReview.quality_score)}`} />
                        <span className={`text-sm font-bold ${scoreColor(preReview.quality_score)}`}>
                          {preReview.quality_score}/10
                        </span>
                      </div>
                    </div>

                    <p className="text-sm text-muted-foreground">{preReview.summary}</p>

                    {preReview.is_duplicate && (
                      <div className="flex items-start gap-2 p-2.5 rounded-lg bg-destructive/10 border border-destructive/20">
                        <AlertTriangle className="w-4 h-4 text-destructive shrink-0 mt-0.5" />
                        <p className="text-xs text-destructive">
                          ⚠️ כפילות אפשרית{preReview.duplicate_of ? `: "${preReview.duplicate_of}"` : ""}
                        </p>
                      </div>
                    )}

                    {preReview.strengths.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-success flex items-center gap-1">
                          <ThumbsUp className="w-3 h-3" /> חוזקות:
                        </p>
                        {preReview.strengths.map((s, i) => (
                          <p key={i} className="text-xs text-muted-foreground mr-5">• {s}</p>
                        ))}
                      </div>
                    )}

                    {preReview.improvements.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-warning flex items-center gap-1">
                          <Lightbulb className="w-3 h-3" /> הצעות לשיפור:
                        </p>
                        {preReview.improvements.map((imp, i) => (
                          <p key={i} className="text-xs text-muted-foreground mr-5">• {imp}</p>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                <Button onClick={handleCreateLesson} disabled={isUploading} className="w-full bg-accent text-accent-foreground hover:bg-accent/90 gap-2">
                  {isUploading && <Loader2 className="w-4 h-4 animate-spin" />}
                  {isUploading ? "מעלה קובץ..." : "צור לקח ושלח לאישור"}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        )}
        </div>
      </div>

      {/* Post-approve AI Recommendations Dialog */}
      <Dialog open={postReviewDialogOpen} onOpenChange={(open) => (open ? setPostReviewDialogOpen(true) : closePostReviewDialog())}>
        <DialogContent dir="rtl" className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
             <DialogTitle className="flex items-center gap-2 font-heading">
               <Brain className="w-5 h-5 text-accent" />
               המלצות AI לאחר אישור
             </DialogTitle>
             <DialogDescription>פרויקטים ורפרנטים מומלצים להפצת הלקח</DialogDescription>
          </DialogHeader>
          {isPostReviewing ? (
            <div className="flex flex-col items-center gap-3 py-8">
              <Loader2 className="w-8 h-8 animate-spin text-accent" />
              <p className="text-sm text-muted-foreground">AI מנתח את הלקח ומייצר המלצות הפצה...</p>
            </div>
          ) : postReview ? (
            <div className="space-y-4 pt-2">
              <p className="text-sm text-muted-foreground">{postReview.summary}</p>

              {postReview.recommended_projects.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">📋 פרויקטים מומלצים להפצה:</h4>
                  {postReview.recommended_projects.map((rp, i) => {
                    const proj = projects.find((p) => p.name === rp.project_name);
                    const projId = proj?.id;
                    return (
                      <label key={i} className="flex items-start gap-3 p-3 rounded-lg bg-muted/50 cursor-pointer hover:bg-muted/70 transition">
                        <Checkbox
                          checked={projId ? selectedAIProjects.includes(projId) : false}
                          onCheckedChange={() => {
                            if (!projId) return;
                            setSelectedAIProjects((prev) =>
                              prev.includes(projId) ? prev.filter((id) => id !== projId) : [...prev, projId]
                            );
                          }}
                          className="mt-0.5"
                        />
                        <div className="flex-1 space-y-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-medium">{rp.project_name}</span>
                            <Badge variant="outline" className={priorityColor[rp.priority]}>
                              עדיפות {priorityLabel[rp.priority]}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground">{rp.reason}</p>
                          {proj && proj.stageIndex !== reviewingLessonStageIndex && (
                            <p className="text-xs text-destructive">נפסל לבחירה אוטומטית כי הפרויקט אינו באותו שלב של הלקח.</p>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}

              {postReview.recommended_referents.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">👤 רפרנטים מומלצים להפצה:</h4>
                  {postReview.recommended_referents.map((referent, i) => {
                    const user = users.find((candidate) => candidate.id === referent.referent_id);
                    const isStageMatch = !!user && user.assignedStageIndexes.includes(reviewingLessonStageIndex);
                    return (
                      <label key={i} className="flex items-start gap-3 p-3 rounded-lg bg-muted/50 cursor-pointer hover:bg-muted/70 transition">
                        <Checkbox
                          checked={selectedAIReferents.includes(referent.referent_id)}
                          onCheckedChange={() => {
                            if (!isStageMatch) return;
                            setSelectedAIReferents((prev) =>
                              prev.includes(referent.referent_id)
                                ? prev.filter((id) => id !== referent.referent_id)
                                : [...prev, referent.referent_id]
                            );
                          }}
                          disabled={!isStageMatch}
                          className="mt-0.5"
                        />
                        <div className="flex-1 space-y-1">
                          <span className="text-sm font-medium">{referent.referent_name}</span>
                          <p className="text-xs text-muted-foreground">{referent.reason}</p>
                          {!isStageMatch && (
                            <p className="text-xs text-destructive">נפסל לבחירה אוטומטית כי לרפרנט אין שיוך לאותו שלב של הלקח.</p>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}

              {(filteredOutAIProjects.length > 0 || filteredOutAIReferents.length > 0) && (
                <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-2">
                  <h4 className="text-sm font-semibold">סוננו אוטומטית עקב חוסר התאמת שלב</h4>
                  {filteredOutAIProjects.length > 0 && (
                    <p className="text-xs text-muted-foreground">פרויקטים: {filteredOutAIProjects.join(", ")}</p>
                  )}
                  {filteredOutAIReferents.length > 0 && (
                    <p className="text-xs text-muted-foreground">רפרנטים: {filteredOutAIReferents.join(", ")}</p>
                  )}
                </div>
              )}

              {postReview.implementation_steps.length > 0 && (
                <div className="space-y-1.5">
                  <h4 className="text-sm font-semibold">🔧 צעדי יישום מומלצים:</h4>
                  {postReview.implementation_steps.map((step, i) => (
                    <p key={i} className="text-xs text-muted-foreground mr-4">
                      {i + 1}. {step}
                    </p>
                  ))}
                </div>
              )}

              {postReview.attention_points.length > 0 && (
                <div className="space-y-1.5">
                  <h4 className="text-sm font-semibold flex items-center gap-1">
                    <AlertTriangle className="w-3.5 h-3.5 text-warning" />
                    נקודות תשומת לב:
                  </h4>
                  {postReview.attention_points.map((pt, i) => (
                    <p key={i} className="text-xs text-muted-foreground mr-4">⚠ {pt}</p>
                  ))}
                </div>
              )}

              {reviewingLessonId && (
                <div className="flex gap-2 pt-2">
                  {autoDistributionTargetCount > 0 && (
                    <Button
                      className="flex-1 gap-2"
                      onClick={() => {
                        distributeLesson(reviewingLessonId, selectedAIProjects, selectedAIReferents, "ai");
                        const parts = [];
                        if (selectedAIProjects.length > 0) parts.push(`${selectedAIProjects.length} פרויקטים`);
                        if (selectedAIReferents.length > 0) parts.push(`${selectedAIReferents.length} רפרנטים`);
                        toast({
                          title: "לקח הופץ בהצלחה ✓",
                          description: `הופץ ל-${parts.join(" ו-")}`,
                        });
                        closePostReviewDialog();
                      }}
                    >
                      <Send className="w-4 h-4" />
                      אשר והפץ ({autoDistributionTargetCount})
                    </Button>
                  )}
                  <Button variant="outline" className="flex-1" onClick={closePostReviewDialog}>
                    {autoDistributionTargetCount > 0 ? "דלג" : "סגור"}
                  </Button>
                </div>
              )}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Manual Distribution Dialog */}
      <Dialog open={manualDistDialogOpen} onOpenChange={setManualDistDialogOpen}>
        <DialogContent dir="rtl" className="max-w-md max-h-[80vh] overflow-y-auto">
          <DialogHeader>
             <DialogTitle className="flex items-center gap-2 font-heading">
               <Send className="w-5 h-5 text-info" />
               הפצה ידנית
             </DialogTitle>
             <DialogDescription>בחר פרויקטים ורפרנטים להפצת הלקח</DialogDescription>
          </DialogHeader>
          {manualDistLessonId && (() => {
            const lesson = lessons.find((l) => l.id === manualDistLessonId);
            if (!lesson) return null;
            const otherProjects = projects.filter((p) => p.id !== lesson.projectId);
            const allReferents = users.filter((u) => u.role === "referent");
            const lessonStageIndex = PROJECT_STAGES.indexOf(lesson.stage);
            const totalSelected = selectedDistProjects.length + selectedDistReferents.length;
            return (
              <div className="space-y-4 pt-2">
                <p className="text-sm text-muted-foreground">
                  בחר את הפרויקטים והרפרנטים שאליהם ברצונך להפיץ את הלקח: <strong>{lesson.title}</strong>
                </p>
                <div className="space-y-2">
                  <div className="flex justify-between items-center">
                    <Label className="text-sm font-medium">פרויקטים:</Label>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-xs h-6"
                      onClick={() => setSelectedDistProjects(
                        selectedDistProjects.length === otherProjects.length ? [] : otherProjects.map((p) => p.id)
                      )}
                    >
                      {selectedDistProjects.length === otherProjects.length ? "נקה הכל" : "בחר הכל"}
                    </Button>
                  </div>
                  <div className="space-y-1.5 max-h-40 overflow-y-auto">
                    {otherProjects.map((p) => (
                      <label key={p.id} className="flex items-center gap-2.5 p-2.5 rounded-lg hover:bg-muted/50 cursor-pointer border border-transparent hover:border-border">
                        <Checkbox
                          checked={selectedDistProjects.includes(p.id)}
                          onCheckedChange={() => toggleDistProject(p.id)}
                        />
                        <div className="flex-1">
                          <span className="text-sm font-medium">{p.name}</span>
                          <span className="text-xs text-muted-foreground mr-2">
                            ({PROJECT_STAGES[p.stageIndex]})
                          </span>
                        </div>
                      </label>
                    ))}
                  </div>
                </div>
                {allReferents.length > 0 && (
                  <div className="space-y-2">
                    <div className="flex justify-between items-center">
                      <Label className="text-sm font-medium">רפרנטים:</Label>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-xs h-6"
                        onClick={() => setSelectedDistReferents(
                          selectedDistReferents.length === allReferents.length ? [] : allReferents.map((r) => r.id)
                        )}
                      >
                        {selectedDistReferents.length === allReferents.length ? "נקה הכל" : "בחר הכל"}
                      </Button>
                    </div>
                    <div className="space-y-1.5 max-h-40 overflow-y-auto">
                      {allReferents.map((r) => {
                        const isStageMatch = r.assignedStageIndexes?.includes(lessonStageIndex);
                        return (
                          <label key={r.id} className={`flex items-center gap-2.5 p-2.5 rounded-lg hover:bg-muted/50 cursor-pointer border ${isStageMatch ? "border-info/30 bg-info/5" : "border-transparent"} hover:border-border`}>
                            <Checkbox
                              checked={selectedDistReferents.includes(r.id)}
                              onCheckedChange={() => toggleDistReferent(r.id)}
                            />
                            <div className="flex-1">
                              <span className="text-sm font-medium">{r.name}</span>
                              {r.assignedStageIndexes?.length > 0 && (
                                <span className="text-xs text-muted-foreground mr-2">
                                  ({r.assignedStageIndexes.map((si) => PROJECT_STAGES[si]).filter(Boolean).join(", ")})
                                </span>
                              )}
                              {isStageMatch && (
                                <span className="text-[10px] text-info mr-1">✓ שלב תואם</span>
                              )}
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                )}
                <div className="flex gap-2 pt-2">
                  <Button
                    className="flex-1 bg-accent text-accent-foreground hover:bg-accent/90 gap-1.5"
                    onClick={handleManualDistribute}
                    disabled={totalSelected === 0}
                  >
                    <Send className="w-4 h-4" />
                    הפץ ({totalSelected})
                  </Button>
                  <Button variant="outline" onClick={() => setManualDistDialogOpen(false)}>
                    ביטול
                  </Button>
                </div>
              </div>
            );
          })()}
        </DialogContent>
      </Dialog>

      {/* Tabs: Repository + Board */}
      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as "repository" | "board")} dir="rtl">
        <TabsList className="bg-muted">
          <TabsTrigger value="repository" className="gap-1.5">
            <BookOpen className="w-4 h-4" />
            מאגר הלקחים
          </TabsTrigger>
          <TabsTrigger value="board" className="gap-1.5">
            <Filter className="w-4 h-4" />
            לקחים בטיפול
          </TabsTrigger>
        </TabsList>

        {/* === Tab 1: Repository === */}
        <TabsContent value="repository" className="mt-4 space-y-4">
          {/* Filters */}
      <div className="flex gap-3 items-center flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input placeholder="חיפוש לקח..." value={search} onChange={(e) => setSearch(e.target.value)} className="pr-10" />
        </div>
        <Filter className="w-4 h-4 text-muted-foreground" />
        <SearchableSelect value={riskFilter} onValueChange={setRiskFilter} className="w-[130px] h-9 text-sm" placeholder="סיכון"
          options={[
            { value: "all", label: "כל הסיכונים" },
            { value: "high", label: "גבוה" },
            { value: "medium", label: "בינוני" },
            { value: "low", label: "נמוך" },
          ]}
        />

        <SearchableSelect value={statusFilter} onValueChange={setStatusFilter} className="w-[130px] h-9 text-sm" placeholder="סטטוס"
          options={[
            { value: "all", label: "כל הסטטוסים" },
            { value: "new", label: "ממתין לאישור" },
            { value: "approved", label: "מאושר" },
            { value: "rejected", label: "נדחה" },
            { value: "distributed", label: "הופץ" },
            { value: "closed", label: "סגור" },
          ]}
        />

        <SearchableSelect value={categoryFilter} onValueChange={setCategoryFilter} className="w-[140px] h-9 text-sm" placeholder="קטגוריה"
          options={[
            { value: "all", label: "כל הקטגוריות" },
            ...categories.map((c) => ({ value: String(c), label: c })),
          ]}
        />

        <SearchableSelect value={stageFilter} onValueChange={setStageFilter} className="w-[150px] h-9 text-sm" placeholder="שלב"
          options={[
            { value: "all", label: "כל השלבים" },
            ...stages.map((s) => ({ value: String(s), label: s })),
          ]}
        />

        <SearchableSelect value={projectFilter} onValueChange={setProjectFilter} className="w-[150px] h-9 text-sm" placeholder="פרויקט"
          options={[
            { value: "all", label: "כל הפרויקטים" },
            ...projects.map((p) => ({ value: String(p.id), label: p.name })),
          ]}
        />

        <SearchableSelect value={stationTypeFilter} onValueChange={setStationTypeFilter} className="w-[150px] h-9 text-sm" placeholder="סוג תחנה"
          options={[
            { value: "all", label: "כל סוגי התחנות" },
            ...Object.entries(stationTypeLabels).map(([value, label]) => ({ value: String(value), label: label })),
          ]}
        />

        <SearchableSelect value={projectTypeFilter} onValueChange={setProjectTypeFilter} className="w-[150px] h-9 text-sm" placeholder="סוג פרויקט"
          options={[
            { value: "all", label: "כל סוגי הפרויקטים" },
            ...Object.entries(projectTypeLabels).map(([value, label]) => ({ value: String(value), label: label })),
          ]}
        />

        {hasActiveFilters && (
          <Button variant="ghost" size="sm" onClick={clearFilters} className="text-muted-foreground">
            נקה מסננים
          </Button>
        )}
      </div>

      {/* Lessons list */}
      <div className="space-y-3">
        {filtered.map((lesson) => {
          const lessonEquipment = equipment.filter((e) => lesson.equipmentIds.includes(e.id));
          const implCount = getImplCount(lesson.id);
          const respondedCount = getRespondedCount(lesson.id);
          const distributedProjects = lesson.distributedTo
            ? projects.filter((p) => lesson.distributedTo!.includes(p.id))
            : [];
          const origin = getLessonOrigin(lesson);

          return (
            <Card
              key={lesson.id}
              className={`shadow-sm hover:shadow-md transition-all duration-200 cursor-pointer group ${
                lessonNeedsAction(lesson.id).needed
                  ? "border-r-4 border-r-warning border-t-0 border-b-0 border-l-0 bg-warning/[0.03] ring-1 ring-warning/20"
                  : "border-0"
              }`}
              onClick={() => navigate(`/lessons/${lesson.id}`)}
            >
              <CardContent className="p-4 md:p-5">
                <div className="flex flex-col gap-3">
                  {/* Title and description - full width */}
                  <div className="flex items-start gap-3 w-full">
                    <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center mt-0.5 shrink-0">
                      <BookOpen className="w-5 h-5 text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className="font-medium text-sm md:text-base leading-relaxed break-words">{lesson.title}</h3>
                        {lessonNeedsAction(lesson.id).needed && (
                          <Badge variant="outline" className="text-[11px] py-0.5 px-2 bg-warning/15 text-warning border-warning/30 animate-pulse font-bold">
                            <Zap className="w-3.5 h-3.5 ml-1" />
                            {lessonNeedsAction(lesson.id).label}
                          </Badge>
                        )}
                        {currentUser.role !== "admin" && origin === "distributed" && (
                          <Badge variant="outline" className="text-[10px] py-0 px-1.5 bg-info/10 text-info border-info/20">
                            📥 הופץ לפרויקט שלך
                          </Badge>
                        )}
                        {currentUser.role !== "admin" && origin === "created" && (
                          <Badge variant="outline" className="text-[10px] py-0 px-1.5 bg-success/10 text-success border-success/20">
                            ✍️ נוצר על ידך
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-1.5 text-xs text-muted-foreground flex-wrap">
                        <span>{lesson.project}</span>
                        <span>·</span>
                        <span>{lesson.stage}</span>
                        <span>·</span>
                        <span>{lesson.category}</span>
                        <span>·</span>
                        <span>{getCreatorName(lesson.createdBy)}</span>
                      </div>
                      {lessonEquipment.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {lessonEquipment.map((eq) => (
                            <Badge key={eq.id} variant="outline" className="text-[10px] py-0 px-1.5 bg-accent/5 border-accent/20">
                              {eq.name}
                            </Badge>
                          ))}
                        </div>
                      )}
                      {distributedProjects.length > 0 && (
                        <div className="mt-1.5 text-xs text-muted-foreground">
                          📤 הופץ ל-{distributedProjects.length} פרויקטים
                          {implCount > 0 && ` · ${respondedCount}/${implCount} תגובות`}
                        </div>
                      )}
                    </div>
                    <ChevronLeft className="w-4 h-4 text-muted-foreground shrink-0 mt-1 hidden md:block" />
                  </div>
                  {/* Badges and actions - wrap below on mobile */}
                  <div className="flex items-center gap-2 flex-wrap mr-13 md:mr-0 md:justify-end">
                    {canApprove && lesson.status === "new" && (
                      <div className="flex gap-1">
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-success hover:bg-success/10" onClick={(e) => handleApprove(e, lesson.id)} title="אשר">
                          <CheckCircle className="w-4 h-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive hover:bg-destructive/10" onClick={(e) => handleReject(e, lesson.id)} title="דחה">
                          <XCircle className="w-4 h-4" />
                        </Button>
                      </div>
                    )}
                    {lesson.status !== "closed" && lesson.status !== "new" && (
                      <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:bg-muted" onClick={(e) => { e.stopPropagation(); closeLesson(lesson.id); toast({ title: "לקח נסגר" }); }} title="סגור לקח">
                        <Archive className="w-4 h-4" />
                      </Button>
                    )}
                    {(lesson.status === "rejected" || lesson.status === "closed") && (currentUser.role === "admin" || lesson.createdBy === currentUser.id) && (
                      <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive hover:bg-destructive/10" onClick={(e) => { e.stopPropagation(); deleteLesson(lesson.id); toast({ title: "לקח נמחק" }); }} title="מחק">
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    )}
                    {currentUser.role !== "admin" && getLessonOrigin(lesson) === "distributed" && (
                      <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:bg-destructive/10 hover:text-destructive" onClick={(e) => {
                        e.stopPropagation();
                        const myProjectIds = currentUser.assignedProjects.filter((pid) => lesson.distributedTo?.includes(pid));
                        myProjectIds.forEach((pid) => removeLessonFromProject(lesson.id, pid));
                        toast({ title: "הלקח הוסר מהפרויקט שלך" });
                      }} title="הסר מהפרויקט שלי">
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    )}
                    {canDistribute && (lesson.status === "approved" || lesson.status === "distributed") && (
                      <Button variant="ghost" size="sm" className="text-info hover:bg-info/10 gap-1" onClick={(e) => openManualDistribute(e, lesson.id)}>
                        <Send className="w-3.5 h-3.5" />
                        הפץ
                      </Button>
                    )}
                    <Badge variant="outline" className={statusColors[lesson.status]}>
                      {statusLabels[lesson.status]}
                    </Badge>
                    <Badge variant="outline" className={riskColors[lesson.risk]}>
                      {riskLabels[lesson.risk]}
                    </Badge>
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
        {filtered.length === 0 && (
          <p className="text-muted-foreground text-center py-12">לא נמצאו לקחים</p>
        )}
      </div>
        </TabsContent>

        {/* === Tab 2: Board (Kanban) === */}
        <TabsContent value="board" className="mt-4 space-y-4">
          {/* Board Filters */}
          <div className="flex gap-3 items-center flex-wrap">
            <div className="relative flex-1 min-w-[200px] max-w-md">
              <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input placeholder="חיפוש לקח..." value={boardSearch} onChange={(e) => setBoardSearch(e.target.value)} className="pr-10" />
            </div>
            <SearchableSelect value={boardProjectFilter} onValueChange={setBoardProjectFilter} className="w-[140px] h-9 text-sm" placeholder="פרויקט"
              options={[
                { value: "all", label: "כל הפרויקטים" },
                ...projects.map((p) => ({ value: String(p.id), label: p.name })),
              ]}
            />
            <SearchableSelect value={boardDomainFilter} onValueChange={setBoardDomainFilter} className="w-[140px] h-9 text-sm" placeholder="תחום מקצועי"
              options={[
                { value: "all", label: "כל התחומים" },
                ...PROFESSIONAL_DOMAINS.map((d) => ({ value: String(d), label: d })),
              ]}
            />
            <SearchableSelect value={boardReferentFilter} onValueChange={setBoardReferentFilter} className="w-[140px] h-9 text-sm" placeholder="רפרנט"
              options={[
                { value: "all", label: "כל הרפרנטים" },
                ...users.filter((u) => u.role === "referent").map((u) => ({ value: String(u.id), label: u.name })),
              ]}
            />
            <SearchableSelect value={boardStatusFilter} onValueChange={setBoardStatusFilter} className="w-[140px] h-9 text-sm" placeholder="סטטוס"
              options={[
                { value: "all", label: "כל הסטטוסים" },
                ...ACTIVE_WORKFLOW_STATUSES.map((s) => ({ value: String(s), label: s })),
              ]}
            />
            <SearchableSelect value={boardSlaFilter} onValueChange={setBoardSlaFilter} className="w-[120px] h-9 text-sm" placeholder="מצב SLA"
              options={[
                { value: "all", label: "כל מצבי SLA" },
                { value: "תקין", label: "תקין" },
                { value: "מתקרב לחריגה", label: "מתקרב לחריגה" },
                { value: "באיחור", label: "באיחור" },
              ]}
            />
            <SearchableSelect value={boardStationTypeFilter} onValueChange={setBoardStationTypeFilter} className="w-[140px] h-9 text-sm" placeholder="סוג תחנה"
              options={[
                { value: "all", label: "כל סוגי התחנות" },
                ...Object.entries(stationTypeLabels).map(([value, label]) => ({ value: String(value), label: label })),
              ]}
            />
            <SearchableSelect value={boardProjectTypeFilter} onValueChange={setBoardProjectTypeFilter} className="w-[140px] h-9 text-sm" placeholder="סוג פרויקט"
              options={[
                { value: "all", label: "כל סוגי הפרויקטים" },
                ...Object.entries(projectTypeLabels).map(([value, label]) => ({ value: String(value), label: label })),
              ]}
            />
            <Button variant="ghost" size="sm" onClick={() => setBoardDateSort((prev) => prev === "desc" ? "asc" : "desc")} className="gap-1.5 text-xs text-muted-foreground">
              <ArrowUpDown className="w-3.5 h-3.5" />
              {boardDateSort === "desc" ? "חדש → ישן" : "ישן → חדש"}
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">{boardLessons.length} לקחים פעילים</p>
          <LessonsKanbanBoard lessons={boardLessons} currentUserId={currentUser.id} currentUserRole={currentUser.role} />
        </TabsContent>
      </Tabs>

      {/* Export Dialog */}
      <Dialog open={exportDialogOpen} onOpenChange={setExportDialogOpen}>
        <DialogContent dir="rtl">
          <DialogHeader>
             <DialogTitle className="font-heading">ייצוא דוח לקחים</DialogTitle>
             <DialogDescription>בחר פורמט ייצוא</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div className="space-y-2">
              <Label>סנן לפי</Label>
              <SearchableSelect value={exportFilterType} onValueChange={(v) => setExportFilterType(v as "all" | "project" | "category")}
                options={[
                  { value: "all", label: "כל הלקחים" },
                  { value: "project", label: "פרויקט" },
                  { value: "category", label: "קטגוריה" },
                ]}
              />
            </div>
            {exportFilterType === "project" && (
              <div className="space-y-2">
                <Label>בחר פרויקט</Label>
                <SearchableSelect value={exportProjectId} onValueChange={setExportProjectId} placeholder="בחר פרויקט"
                  options={[
                    ...projects.map((p) => ({ value: String(p.id), label: p.name })),
                  ]}
                />
              </div>
            )}
            {exportFilterType === "category" && (
              <div className="space-y-2">
                <Label>בחר קטגוריה</Label>
                <SearchableSelect value={exportCategory} onValueChange={setExportCategory} placeholder="בחר קטגוריה"
                  options={[
                    ...categories.map((c) => ({ value: String(c), label: c })),
                  ]}
                />
              </div>
            )}
            <div className="flex gap-2">
              <Button onClick={handleExport} variant="outline" className="flex-1 gap-2">
                <Download className="w-4 h-4" />
                CSV
              </Button>
              <Button onClick={handleExportPDFEmail} disabled={isSendingEmail} className="flex-1 gap-2">
                {isSendingEmail ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mail className="w-4 h-4" />}
                {isSendingEmail ? "שולח..." : "שלח PDF במייל"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default Lessons;
