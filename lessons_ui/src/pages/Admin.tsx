import { useEffect, useState } from "react";

import { Users, FolderKanban, Pencil, Trash2, Wrench, BookOpen, Plus, Brain, Loader2, Tag, MessageSquare, CheckCircle, History, Settings, Lightbulb, Newspaper } from "lucide-react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { roleLabels, useUser, PROJECT_STAGES, projectTypeLabels, stationTypeLabels, defaultEmailPreferences, feedbackTypeLabels, feedbackStatusLabels } from "@/context/UserContext";
import { useSearchParams } from "react-router-dom";
import { feedbackTypeColors, feedbackStatusColors } from "@/lib/constants";
import type { MockUser, UserRole, ProjectType, StationType, Project, Feedback } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";
import { supabase } from "@/integrations/supabase/client";
import ActivityHistoryTab from "@/components/ActivityHistoryTab";
import FormFieldManager from "@/components/FormFieldManager";
import AdminAIInsightsTab from "@/components/AdminAIInsightsTab";
import AdminLessonQualityTab from "@/components/AdminLessonQualityTab";
import NewsletterPanel from "@/components/admin/NewsletterPanel";
import SiteCombobox from "@/components/SiteCombobox";
import SearchableSelect from "@/components/ui/searchable-select";

const roleBadgeColors: Record<string, string> = {
  admin: "bg-accent/10 text-accent border-accent/20",
  project_manager: "bg-primary/10 text-primary border-primary/20",
  referent: "bg-info/10 text-info border-info/20",
  viewer: "bg-muted text-muted-foreground border-muted-foreground/20",
};

const Admin = () => {
  const [searchParams] = useSearchParams();
  const feedbackIdFromQuery = Number(searchParams.get("feedbackId"));
  const highlightedFeedbackId = Number.isInteger(feedbackIdFromQuery) ? feedbackIdFromQuery : null;
  const requestedTab = searchParams.get("tab") === "feedback" || highlightedFeedbackId ? "feedback" : "users";
  const { currentUser, projects, equipment, addEquipment, updateEquipment, removeEquipment, renameCategory, deleteCategory, lessons, users, updateUser, addUser, deleteUser, deleteProject, addProject, updateProject, updateProjectEquipment, lessonCategories, addLessonCategory, renameLessonCategory, deleteLessonCategory, feedbacks, updateFeedbackStatus, deleteFeedback } = useUser();
  const [activeTab, setActiveTab] = useState(requestedTab);
  const [newEqName, setNewEqName] = useState("");
  const [newEqCategory, setNewEqCategory] = useState("");
  const [newEqProjects, setNewEqProjects] = useState<number[]>([]);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [selectedEqId, setSelectedEqId] = useState<number | null>(null);

  // Category management state
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [editCategoryName, setEditCategoryName] = useState("");
  const [deleteCategoryName, setDeleteCategoryName] = useState<string | null>(null);

  // Lesson category management state
  const [newLessonCategory, setNewLessonCategory] = useState("");
  const [editingLessonCategory, setEditingLessonCategory] = useState<string | null>(null);
  const [editLessonCategoryName, setEditLessonCategoryName] = useState("");
  const [deleteLessonCategoryName, setDeleteLessonCategoryName] = useState<string | null>(null);
  const [aiSuggestions, setAiSuggestions] = useState<{ name: string; description: string }[]>([]);
  const [isLoadingAI, setIsLoadingAI] = useState(false);

  // Feedback management state
  const [feedbackFilter, setFeedbackFilter] = useState<"all" | "open" | "in_progress" | "closed">("all");
  const [feedbackNotesId, setFeedbackNotesId] = useState<number | null>(null);
  const [feedbackNotes, setFeedbackNotes] = useState("");
  const [deleteFeedbackId, setDeleteFeedbackId] = useState<number | null>(null);

  // User edit state
  const [editingUser, setEditingUser] = useState<MockUser | null>(null);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editRole, setEditRole] = useState<UserRole>("project_manager");
  const [editProjects, setEditProjects] = useState<number[]>([]);

  // Project edit/add state
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState<number | null>(null);
  const [projName, setProjName] = useState("");
  const [projType, setProjType] = useState<ProjectType>("new_build");
  const [projStation, setProjStation] = useState<StationType>("closed");
  const [projRisk, setProjRisk] = useState<"high" | "medium" | "low">("low");
  const [projManager, setProjManager] = useState("");
  const [projStage, setProjStage] = useState(0);
  const [projEquipment, setProjEquipment] = useState<number[]>([]);
  const [projSite, setProjSite] = useState("");
  const [deleteProjectId, setDeleteProjectId] = useState<number | null>(null);

  // New user dialog state
  const [newUserDialogOpen, setNewUserDialogOpen] = useState(false);
  const [newUserName, setNewUserName] = useState("");
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserRole, setNewUserRole] = useState<UserRole>("project_manager");
  const [newUserProjects, setNewUserProjects] = useState<number[]>([]);
  const [newUserEquipment, setNewUserEquipment] = useState<number[]>([]);
  const [newUserStages, setNewUserStages] = useState<number[]>([]);
  const [deleteUserId, setDeleteUserId] = useState<string | null>(null);
  const [editEquipment, setEditEquipment] = useState<number[]>([]);
  const [editStages, setEditStages] = useState<number[]>([]);

  useEffect(() => {
    setActiveTab(requestedTab);

    if (!highlightedFeedbackId) return;

    setFeedbackFilter("all");
    const timeoutId = window.setTimeout(() => {
      document.getElementById(`feedback-${highlightedFeedbackId}`)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 150);

    return () => window.clearTimeout(timeoutId);
  }, [requestedTab, highlightedFeedbackId]);

  const handleAddUser = async () => {
    if (!newUserName.trim() || !newUserEmail.trim()) {
      toast({ title: "שגיאה", description: "נא למלא שם ואימייל", variant: "destructive" });
      return;
    }
    await addUser({
      name: newUserName.trim(),
      email: newUserEmail.trim(),
      role: newUserRole,
      assignedProjects: newUserRole === "admin" ? projects.map((p) => p.id) : newUserRole === "project_manager" ? newUserProjects : [],
      assignedEquipmentIds: newUserRole === "referent" ? newUserEquipment : [],
      assignedStageIndexes: newUserRole === "referent" ? newUserStages : [],
      emailPreferences: defaultEmailPreferences,
    });
    toast({ title: "משתמש חדש נוצר ✓" });
    setNewUserDialogOpen(false);
    setNewUserName("");
    setNewUserEmail("");
    setNewUserRole("project_manager");
    setNewUserProjects([]);
    setNewUserEquipment([]);
    setNewUserStages([]);
  };

  const toggleNewUserProject = (pid: number) => {
    setNewUserProjects((prev) => prev.includes(pid) ? prev.filter((id) => id !== pid) : [...prev, pid]);
  };

  const openEditUser = (user: MockUser) => {
    setEditingUser(user);
    setEditName(user.name);
    setEditEmail(user.email);
    setEditRole(user.role);
    setEditProjects(user.assignedProjects);
    setEditEquipment(user.assignedEquipmentIds || []);
    setEditStages(user.assignedStageIndexes || []);
  };

  const saveEditUser = () => {
    if (!editingUser || !editName.trim()) return;
    updateUser(editingUser.id, {
      name: editName.trim(),
      email: editEmail.trim(),
      role: editRole,
      assignedProjects: editRole === "admin" ? projects.map((p) => p.id) : editRole === "project_manager" ? editProjects : [],
      assignedEquipmentIds: editRole === "referent" ? editEquipment : [],
      assignedStageIndexes: editRole === "referent" ? editStages : [],
    });
    setEditingUser(null);
    toast({ title: "המשתמש עודכן ✓" });
  };

  const toggleEditProject = (projectId: number) => {
    setEditProjects((prev) =>
      prev.includes(projectId) ? prev.filter((id) => id !== projectId) : [...prev, projectId]
    );
  };

  const openProjectDialog = (project?: Project) => {
    if (project) {
      setEditingProjectId(project.id);
      setProjName(project.name);
      setProjType(project.projectType);
      setProjStation(project.stationType);
      setProjRisk(project.risk as "high" | "medium" | "low");
      setProjManager(project.managerId);
      setProjStage(project.stageIndex);
      setProjEquipment(project.equipmentIds);
      setProjSite(project.site || "");
    } else {
      setEditingProjectId(null);
      setProjName("");
      setProjType("new_build");
      setProjStation("closed");
      setProjRisk("low");
      setProjManager(users.find((u) => u.role === "project_manager")?.id || users[0]?.id || "");
      setProjStage(0);
      setProjEquipment([]);
      setProjSite("");
    }
    setProjectDialogOpen(true);
  };

  const saveProject = () => {
    if (!projName.trim() || !projManager) return;
    if (editingProjectId !== null) {
      updateProject(editingProjectId, {
        name: projName.trim(),
        projectType: projType,
        stationType: projStation,
        risk: projRisk,
        managerId: projManager,
        site: projSite || undefined,
      });
      updateProjectEquipment(editingProjectId, projEquipment);
      toast({ title: "הפרויקט עודכן ✓" });
    } else {
      addProject({
        name: projName.trim(),
        stageIndex: projStage,
        stageIndexes: [projStage],
        risk: projRisk,
        equipmentIds: projEquipment,
        projectType: projType,
        stationType: projStation,
        managerId: projManager,
        site: projSite || undefined,
      });
      toast({ title: "פרויקט חדש נוצר ✓" });
    }
    setProjectDialogOpen(false);
  };

  const toggleProjEquipment = (eqId: number) => {
    setProjEquipment((prev) =>
      prev.includes(eqId) ? prev.filter((id) => id !== eqId) : [...prev, eqId]
    );
  };

  const handleAddEquipment = async () => {
    if (!newEqName.trim()) return;
    await addEquipment({ name: newEqName.trim(), category: newEqCategory.trim() || "כללי" }, newEqProjects);
    setNewEqName("");
    setNewEqCategory("");
    setNewEqProjects([]);
    setAddDialogOpen(false);
    toast({ title: "פריט ציוד נוסף ✓" });
  };

  const toggleNewEqProject = (pid: number) => {
    setNewEqProjects((prev) => prev.includes(pid) ? prev.filter((id) => id !== pid) : [...prev, pid]);
  };

  const handleRenameCategory = async () => {
    if (!editingCategory || !editCategoryName.trim()) return;
    await renameCategory(editingCategory, editCategoryName.trim());
    setEditingCategory(null);
    toast({ title: "קטגוריה שונתה ✓" });
  };

  const handleDeleteCategory = async () => {
    if (!deleteCategoryName) return;
    await deleteCategory(deleteCategoryName);
    setDeleteCategoryName(null);
    toast({ title: "קטגוריה נמחקה ✓" });
  };

  const getEquipmentLessons = (eqId: number) => {
    const projectIds = projects.filter((p) => p.equipmentIds.includes(eqId)).map((p) => p.id);
    return lessons.filter((l) => l.projectId !== null && projectIds.includes(l.projectId));
  };

  const getEquipmentProjects = (eqId: number) => {
    return projects.filter((p) => p.equipmentIds.includes(eqId));
  };

  const categories = [...new Set(equipment.map((e) => e.category))];

  const handleAddLessonCategory = async () => {
    if (!newLessonCategory.trim()) return;
    if (lessonCategories.includes(newLessonCategory.trim())) {
      toast({ title: "שגיאה", description: "קטגוריה זו כבר קיימת", variant: "destructive" });
      return;
    }
    try {
      await addLessonCategory(newLessonCategory.trim());
      toast({ title: "קטגוריה נוספה ✓" });
      setNewLessonCategory("");
    } catch {
      toast({ title: "שגיאה בהוספת קטגוריה", variant: "destructive" });
    }
  };

  const handleRenameLessonCategory = async () => {
    if (!editLessonCategoryName.trim() || !editingLessonCategory) return;
    try {
      await renameLessonCategory(editingLessonCategory, editLessonCategoryName.trim());
      toast({ title: "קטגוריה עודכנה ✓" });
      setEditingLessonCategory(null);
    } catch {
      toast({ title: "שגיאה בעדכון קטגוריה", variant: "destructive" });
    }
  };

  const handleDeleteLessonCategory = async () => {
    if (!deleteLessonCategoryName) return;
    const lessonsInCategory = lessons.filter((l) => l.category === deleteLessonCategoryName);
    if (lessonsInCategory.length > 0) {
      toast({ title: "שגיאה", description: `יש ${lessonsInCategory.length} לקחים בקטגוריה זו. שנה את הקטגוריה שלהם לפני מחיקה.`, variant: "destructive" });
      setDeleteLessonCategoryName(null);
      return;
    }
    try {
      await deleteLessonCategory(deleteLessonCategoryName);
      toast({ title: "קטגוריה נמחקה ✓" });
      setDeleteLessonCategoryName(null);
    } catch {
      toast({ title: "שגיאה במחיקת קטגוריה", variant: "destructive" });
    }
  };

  const handleAISuggestCategories = async () => {
    setIsLoadingAI(true);
    setAiSuggestions([]);
    try {
      const { data, error } = await supabase.functions.invoke("suggest-categories", {
        body: {
          existingCategories: lessonCategories,
          lessons: lessons.slice(0, 20).map((l) => ({ title: l.title, category: l.category })),
        },
      });
      if (error) throw error;
      setAiSuggestions(data.suggestions || []);
    } catch {
      toast({ title: "שגיאה בקבלת המלצות AI", variant: "destructive" });
    } finally {
      setIsLoadingAI(false);
    }
  };

  const handleAddAISuggestion = async (name: string) => {
    if (lessonCategories.includes(name)) {
      toast({ title: "קטגוריה כבר קיימת", variant: "destructive" });
      return;
    }
    try {
      await addLessonCategory(name);
      setAiSuggestions((prev) => prev.filter((s) => s.name !== name));
      toast({ title: `קטגוריה "${name}" נוספה ✓` });
    } catch {
      toast({ title: "שגיאה בהוספת קטגוריה", variant: "destructive" });
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-heading font-bold">ניהול מערכת</h1>
        <p className="text-muted-foreground mt-1">ניהול משתמשים, פרויקטים, ציוד והרשאות</p>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} dir="rtl">
        <TabsList className="bg-muted flex flex-wrap h-auto gap-1 p-1">
          <TabsTrigger value="users" className="gap-1.5 text-xs sm:text-sm">
            <Users className="w-4 h-4" />
            <span className="hidden sm:inline">משתמשים</span>
          </TabsTrigger>
          <TabsTrigger value="projects" className="gap-1.5 text-xs sm:text-sm">
            <FolderKanban className="w-4 h-4" />
            <span className="hidden sm:inline">פרויקטים</span>
          </TabsTrigger>
          <TabsTrigger value="equipment" className="gap-1.5 text-xs sm:text-sm">
            <Wrench className="w-4 h-4" />
            <span className="hidden sm:inline">ציוד ונושאים</span>
          </TabsTrigger>
          <TabsTrigger value="lesson_categories" className="gap-1.5 text-xs sm:text-sm">
            <Tag className="w-4 h-4" />
            <span className="hidden sm:inline">קטגוריות</span>
          </TabsTrigger>
          {currentUser.role === "admin" && (
            <TabsTrigger value="activity" className="gap-1.5 text-xs sm:text-sm">
              <History className="w-4 h-4" />
              <span className="hidden sm:inline">היסטוריית פעילות</span>
            </TabsTrigger>
          )}
          <TabsTrigger value="feedback" className="gap-1.5 text-xs sm:text-sm relative">
            <MessageSquare className="w-4 h-4" />
            <span className="hidden sm:inline">פידבק</span>
            {feedbacks.filter((f) => f.status === "open").length > 0 && (
              <span className="absolute -top-1 -left-1 w-4 h-4 bg-destructive text-destructive-foreground text-[10px] rounded-full flex items-center justify-center">
                {feedbacks.filter((f) => f.status === "open").length}
              </span>
            )}
          </TabsTrigger>
          {currentUser.role === "admin" && (
            <>
              <TabsTrigger value="form_lesson" className="gap-1.5 text-xs sm:text-sm">
                <Settings className="w-4 h-4" />
                <span className="hidden sm:inline">טופס לקח</span>
              </TabsTrigger>
              <TabsTrigger value="form_project" className="gap-1.5 text-xs sm:text-sm">
                <Settings className="w-4 h-4" />
                <span className="hidden sm:inline">טופס פרויקט</span>
              </TabsTrigger>
              <TabsTrigger value="ai_insights" className="gap-1.5 text-xs sm:text-sm">
                <Lightbulb className="w-4 h-4" />
                <span className="hidden sm:inline">תובנות AI</span>
              </TabsTrigger>
              <TabsTrigger value="lesson_quality" className="gap-1.5 text-xs sm:text-sm">
                <BookOpen className="w-4 h-4" />
                <span className="hidden sm:inline">איכות לקחים</span>
              </TabsTrigger>
              <TabsTrigger value="newsletter" className="gap-1.5 text-xs sm:text-sm">
                <Newspaper className="w-4 h-4" />
                <span className="hidden sm:inline">עלון פעילות</span>
              </TabsTrigger>
            </>
          )}
        </TabsList>

        <TabsContent value="users" className="mt-6">
          <Card className="border-0 shadow-sm">
            <CardHeader className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
              <CardTitle className="text-base font-heading">משתמשים</CardTitle>
              <Button size="sm" className="bg-accent text-accent-foreground hover:bg-accent/90" onClick={() => setNewUserDialogOpen(true)}>
                + משתמש חדש
              </Button>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {users.map((user) => (
                   <div key={user.id} className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 rounded-xl bg-muted/50">
                     <div className="flex items-center gap-3 min-w-0">
                       <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                         <span className="text-sm font-bold text-primary">{user.name[0]}</span>
                       </div>
                       <div className="min-w-0">
                         <p className="text-sm font-medium truncate">{user.name}</p>
                         <p className="text-xs text-muted-foreground truncate">{user.email}</p>
                          {user.role === "project_manager" && user.assignedProjects.length > 0 && (
                            <p className="text-xs text-muted-foreground mt-0.5 truncate">
                              פרויקטים: {user.assignedProjects.map((pid) => projects.find((p) => p.id === pid)?.name).filter(Boolean).join(", ")}
                            </p>
                          )}
                           {user.role === "referent" && user.assignedStageIndexes && user.assignedStageIndexes.length > 0 && (
                             <p className="text-xs text-muted-foreground mt-0.5 truncate">
                               שלבים: {user.assignedStageIndexes.map((si) => PROJECT_STAGES[si]).filter(Boolean).join(", ")}
                             </p>
                           )}
                       </div>
                     </div>
                     <div className="flex items-center gap-2 sm:gap-3 mr-13 sm:mr-0">
                       <Badge variant="outline" className={roleBadgeColors[user.role]}>
                         {roleLabels[user.role]}
                       </Badge>
                       <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEditUser(user)}>
                         <Pencil className="w-4 h-4" />
                       </Button>
                       <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => setDeleteUserId(user.id)}>
                         <Trash2 className="w-4 h-4" />
                       </Button>
                     </div>
                   </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="projects" className="mt-6">
          <Card className="border-0 shadow-sm">
             <CardHeader className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
              <CardTitle className="text-base font-heading">פרויקטים</CardTitle>
              <Button size="sm" className="bg-accent text-accent-foreground hover:bg-accent/90 gap-1.5" onClick={() => openProjectDialog()}>
                <Plus className="w-4 h-4" />
                פרויקט חדש
              </Button>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {projects.map((p) => {
                  const pm = users.find((u) => u.id === p.managerId);
                  const projectEq = equipment.filter((e) => p.equipmentIds.includes(e.id));
                  return (
                     <div key={p.id} className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 rounded-xl bg-muted/50">
                       <div className="flex items-center gap-3 min-w-0">
                         <div className="w-10 h-10 rounded-xl bg-primary flex items-center justify-center shrink-0">
                           <FolderKanban className="w-5 h-5 text-primary-foreground" />
                         </div>
                         <div className="min-w-0">
                           <p className="text-sm font-medium truncate">{p.name}</p>
                           <p className="text-xs text-muted-foreground truncate">
                             מנהל: {pm?.name || "—"} · {projectTypeLabels[p.projectType]} · {stationTypeLabels[p.stationType]}
                           </p>
                           {projectEq.length > 0 && (
                             <div className="flex flex-wrap gap-1 mt-1">
                               {projectEq.map((e) => (
                                 <Badge key={e.id} variant="outline" className="text-[10px] py-0 px-1.5">
                                   {e.name}
                                 </Badge>
                               ))}
                             </div>
                           )}
                         </div>
                       </div>
                       <div className="flex items-center gap-2 mr-13 sm:mr-0 shrink-0">
                         <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openProjectDialog(p)}>
                           <Pencil className="w-4 h-4" />
                         </Button>
                         <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => setDeleteProjectId(p.id)}>
                           <Trash2 className="w-4 h-4" />
                         </Button>
                       </div>
                     </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="equipment" className="mt-6 space-y-6">
          <Card className="border-0 shadow-sm">
             <CardHeader className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
              <CardTitle className="text-base font-heading">ציוד ונושאים כלליים</CardTitle>
              <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
                <DialogTrigger asChild>
                  <Button size="sm" className="bg-accent text-accent-foreground hover:bg-accent/90 gap-1.5">
                    <Plus className="w-4 h-4" />
                    הוסף פריט
                  </Button>
                </DialogTrigger>
                <DialogContent dir="rtl" className="max-w-md max-h-[85vh] overflow-y-auto">
                  <DialogHeader>
                     <DialogTitle>הוסף ציוד / נושא חדש</DialogTitle>
                     <DialogDescription>הזן שם, קטגוריה וקשר לפרויקטים</DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4 pt-2">
                    <div>
                      <label className="text-sm font-medium mb-1.5 block">שם</label>
                      <Input value={newEqName} onChange={(e) => setNewEqName(e.target.value)} placeholder="לדוגמה: מערכות חשמל" />
                    </div>
                    <div>
                      <label className="text-sm font-medium mb-1.5 block">קטגוריה</label>
                      <Input value={newEqCategory} onChange={(e) => setNewEqCategory(e.target.value)} placeholder="לדוגמה: תשתיות" />
                      {categories.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mt-2">
                          {categories.map((cat) => (
                            <Badge
                              key={cat}
                              variant="outline"
                              className="cursor-pointer hover:bg-accent/10 text-xs"
                              onClick={() => setNewEqCategory(cat)}
                            >
                              {cat}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                    <div>
                      <label className="text-sm font-medium mb-1.5 block">קשר לפרויקטים</label>
                      <div className="space-y-2 max-h-40 overflow-y-auto">
                        {projects.map((p) => (
                          <label key={p.id} className="flex items-center gap-2 cursor-pointer">
                            <Checkbox
                              checked={newEqProjects.includes(p.id)}
                              onCheckedChange={() => toggleNewEqProject(p.id)}
                            />
                            <span className="text-sm">{p.name}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                    <Button onClick={handleAddEquipment} className="w-full">הוסף</Button>
                  </div>
                </DialogContent>
              </Dialog>
            </CardHeader>
            <CardContent>
              {categories.map((cat) => (
                <div key={cat} className="mb-4 last:mb-0">
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{cat}</h4>
                    <div className="flex items-center gap-1">
                      <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => { setEditingCategory(cat); setEditCategoryName(cat); }}>
                        <Pencil className="w-3 h-3" />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-6 w-6 text-destructive" onClick={() => setDeleteCategoryName(cat)}>
                        <Trash2 className="w-3 h-3" />
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-2">
                    {equipment
                      .filter((e) => e.category === cat)
                      .map((eq) => {
                        const eqProjects = getEquipmentProjects(eq.id);
                        const eqLessons = getEquipmentLessons(eq.id);
                        const isSelected = selectedEqId === eq.id;
                        return (
                          <div key={eq.id}>
                            <div
                              className={`flex items-center justify-between p-3 rounded-xl cursor-pointer transition-colors ${isSelected ? "bg-accent/10 ring-1 ring-accent/30" : "bg-muted/50 hover:bg-muted"}`}
                              onClick={() => setSelectedEqId(isSelected ? null : eq.id)}
                            >
                              <div className="flex items-center gap-3">
                                <Wrench className="w-4 h-4 text-muted-foreground" />
                                <div>
                                  <p className="text-sm font-medium">{eq.name}</p>
                                  <p className="text-xs text-muted-foreground">
                                    {eqProjects.length} פרויקטים · {eqLessons.length} לקחים
                                  </p>
                                </div>
                              </div>
                              <div className="flex items-center gap-2">
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7 text-destructive"
                                  onClick={(e) => { e.stopPropagation(); removeEquipment(eq.id); }}
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </Button>
                              </div>
                            </div>
                            {isSelected && (
                              <div className="mt-2 mr-7 space-y-2 pb-2">
                                {eqProjects.length > 0 && (
                                  <div className="flex flex-wrap gap-1.5">
                                    {eqProjects.map((p) => (
                                      <Badge key={p.id} variant="outline" className="bg-primary/5 text-primary border-primary/20 text-xs">
                                        {p.name}
                                      </Badge>
                                    ))}
                                  </div>
                                )}
                                {eqLessons.length > 0 ? (
                                  <div className="space-y-1.5">
                                    <p className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                                      <BookOpen className="w-3.5 h-3.5" />
                                      לקחים רלוונטיים:
                                    </p>
                                    {eqLessons.slice(0, 5).map((l) => (
                                      <div key={l.id} className="text-xs p-2 rounded-lg bg-background border">
                                        <span className="font-medium">{l.title}</span>
                                        <span className="text-muted-foreground"> · {l.project} · {l.stage}</span>
                                      </div>
                                    ))}
                                    {eqLessons.length > 5 && (
                                      <p className="text-xs text-muted-foreground">ועוד {eqLessons.length - 5} לקחים...</p>
                                    )}
                                  </div>
                                ) : (
                                  <p className="text-xs text-muted-foreground">אין לקחים רלוונטיים עדיין</p>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </div>
                </div>
              ))}
              {equipment.length === 0 && (
                <p className="text-sm text-muted-foreground text-center py-8">אין פריטי ציוד/נושאים. הוסף פריט חדש.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="lesson_categories" className="mt-6">
          <Card className="border-0 shadow-sm">
             <CardHeader className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
              <CardTitle className="text-base font-heading">קטגוריות לקחים</CardTitle>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="gap-1.5" onClick={handleAISuggestCategories} disabled={isLoadingAI}>
                  {isLoadingAI ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Brain className="w-3.5 h-3.5" />}
                  המלצת AI
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="flex gap-2 mb-4">
                <Input
                  value={newLessonCategory}
                  onChange={(e) => setNewLessonCategory(e.target.value)}
                  placeholder="שם קטגוריה חדשה"
                  onKeyDown={(e) => e.key === "Enter" && handleAddLessonCategory()}
                />
                <Button onClick={handleAddLessonCategory} size="sm" className="gap-1.5 whitespace-nowrap">
                  <Plus className="w-3.5 h-3.5" />
                  הוסף
                </Button>
              </div>
              {aiSuggestions.length > 0 && (
                <div className="mb-4 p-4 rounded-xl bg-accent/5 border border-accent/20">
                  <h4 className="text-sm font-medium mb-3 flex items-center gap-1.5">
                    <Brain className="w-4 h-4 text-accent" />
                    המלצות AI
                  </h4>
                  <div className="space-y-2">
                    {aiSuggestions.map((s) => (
                      <div key={s.name} className="flex items-center justify-between p-3 rounded-lg bg-background">
                        <div>
                          <p className="text-sm font-medium">{s.name}</p>
                          <p className="text-xs text-muted-foreground">{s.description}</p>
                        </div>
                        <Button size="sm" variant="outline" className="gap-1 text-xs" onClick={() => handleAddAISuggestion(s.name)}>
                          <Plus className="w-3 h-3" />
                          הוסף
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="space-y-2">
                {lessonCategories.map((cat) => {
                  const count = lessons.filter((l) => l.category === cat).length;
                  return (
                    <div key={cat} className="flex items-center justify-between p-3 rounded-xl bg-muted/50">
                      <div className="flex items-center gap-3">
                        <Tag className="w-4 h-4 text-muted-foreground" />
                        <div>
                          <p className="text-sm font-medium">{cat}</p>
                          <p className="text-xs text-muted-foreground">{count} לקחים</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { setEditingLessonCategory(cat); setEditLessonCategoryName(cat); }}>
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => setDeleteLessonCategoryName(cat)}>
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
                {lessonCategories.length === 0 && (
                  <p className="text-sm text-muted-foreground text-center py-8">אין קטגוריות. הוסף קטגוריה חדשה או בקש המלצת AI.</p>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {currentUser.role === "admin" && (
          <TabsContent value="activity" className="mt-6">
            <ActivityHistoryTab />
          </TabsContent>
        )}

        <TabsContent value="feedback" className="mt-6">
          <Card className="border-0 shadow-sm">
             <CardHeader className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
              <CardTitle className="text-base font-heading">פידבק ממנהלי פרויקטים</CardTitle>
               <div className="flex flex-wrap gap-1.5">
                {(["all", "open", "in_progress", "closed"] as const).map((f) => (
                  <Button
                    key={f}
                    size="sm"
                    variant={feedbackFilter === f ? "default" : "outline"}
                    className="text-xs"
                    onClick={() => setFeedbackFilter(f)}
                  >
                    {f === "all" ? "הכל" : feedbackStatusLabels[f]}
                    {f === "open" && feedbacks.filter((fb) => fb.status === "open").length > 0 && (
                      <span className="mr-1 bg-destructive text-destructive-foreground rounded-full w-4 h-4 text-[10px] flex items-center justify-center">
                        {feedbacks.filter((fb) => fb.status === "open").length}
                      </span>
                    )}
                  </Button>
                ))}
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {feedbacks
                  .filter((fb) => feedbackFilter === "all" || fb.status === feedbackFilter)
                  .map((fb) => {
                    return (
                      <div id={`feedback-${fb.id}`} key={fb.id} className={`p-4 rounded-xl border transition-all ${fb.status === "open" ? "bg-info/5 border-info/20" : fb.status === "in_progress" ? "bg-warning/5 border-warning/20" : "bg-muted/30 border-muted"} ${highlightedFeedbackId === fb.id ? "ring-2 ring-primary shadow-md" : ""}`}>
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex-1 space-y-2">
                            <div className="flex items-center gap-2 flex-wrap">
                              <Badge variant="outline" className={feedbackTypeColors[fb.type]}>
                                {feedbackTypeLabels[fb.type]}
                              </Badge>
                              <Badge variant="outline" className={feedbackStatusColors[fb.status]}>
                                {feedbackStatusLabels[fb.status]}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                מאת {fb.userName} · {new Date(fb.createdAt).toLocaleDateString("he-IL")}
                              </span>
                              {highlightedFeedbackId === fb.id && (
                                <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20">
                                  נפתח מהתראה
                                </Badge>
                              )}
                            </div>
                            <h4 className="font-medium text-sm">{fb.title}</h4>
                            {fb.description && <p className="text-xs text-muted-foreground">{fb.description}</p>}
                            {fb.adminNotes && (
                              <div className="p-2 rounded-lg bg-primary/5 border border-primary/10">
                                <p className="text-xs text-primary font-medium">הערת מנהל:</p>
                                <p className="text-xs text-muted-foreground mt-0.5">{fb.adminNotes}</p>
                              </div>
                            )}
                          </div>
                          <div className="flex flex-col gap-1.5 shrink-0">
                            {fb.status === "open" && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="text-xs gap-1"
                                onClick={() => updateFeedbackStatus(fb.id, "in_progress")}
                              >
                                בטיפול
                              </Button>
                            )}
                            {fb.status !== "closed" && (
                              <>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="text-xs gap-1"
                                  onClick={() => { setFeedbackNotesId(fb.id); setFeedbackNotes(fb.adminNotes); }}
                                >
                                  <Pencil className="w-3 h-3" />
                                  הערה
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="text-xs gap-1 text-success"
                                  onClick={() => updateFeedbackStatus(fb.id, "closed")}
                                >
                                  <CheckCircle className="w-3 h-3" />
                                  סגור
                                </Button>
                              </>
                            )}
                            <Button
                              size="sm"
                              variant="outline"
                              className="text-xs gap-1 text-destructive"
                              onClick={() => setDeleteFeedbackId(fb.id)}
                            >
                              <Trash2 className="w-3 h-3" />
                              מחק
                            </Button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                {feedbacks.filter((fb) => feedbackFilter === "all" || fb.status === feedbackFilter).length === 0 && (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {feedbackFilter === "all" ? "אין פידבקים עדיין" : `אין פידבקים בסטטוס "${feedbackStatusLabels[feedbackFilter]}"`}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {currentUser.role === "admin" && (
          <TabsContent value="form_lesson" className="mt-6">
            <FormFieldManager
              formType="lesson"
              title="ניהול טופס לקח"
              description="ניהול דינמי של שדות טופס יצירת לקח: שם תצוגה, חובה/אופציונלי, הצגה/הסתרה, סדר הופעה וניהול ערכי רשימה"
            />
          </TabsContent>
        )}

        {currentUser.role === "admin" && (
          <TabsContent value="form_project" className="mt-6">
            <FormFieldManager
              formType="project"
              title="ניהול טופס יצירת פרויקט"
              description="ניהול דינמי של שדות טופס יצירת פרויקט: שם תצוגה, חובה/אופציונלי, הצגה/הסתרה, סדר הופעה וניהול ערכי רשימה"
            />
          </TabsContent>
        )}

        {currentUser.role === "admin" && (
          <TabsContent value="ai_insights" className="mt-6">
            <Card className="border-0 shadow-sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base font-heading">
                  <Lightbulb className="w-5 h-5 text-accent" />
                  תובנות AI לאישור
                </CardTitle>
              </CardHeader>
              <CardContent>
                <AdminAIInsightsTab />
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {currentUser.role === "admin" && (
          <TabsContent value="lesson_quality" className="mt-6">
            <AdminLessonQualityTab />
          </TabsContent>
        )}

        {currentUser.role === "admin" && (
          <TabsContent value="newsletter" className="mt-6">
            <NewsletterPanel />
          </TabsContent>
        )}
      </Tabs>

      {/* Edit User Dialog */}
      <Dialog open={!!editingUser} onOpenChange={(open) => !open && setEditingUser(null)}>
        <DialogContent dir="rtl">
          <DialogHeader>
             <DialogTitle>עריכת משתמש</DialogTitle>
             <DialogDescription>שנה פרטי משתמש, תפקיד ופרויקטים</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם</Label>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label>אימייל</Label>
              <Input value={editEmail} onChange={(e) => setEditEmail(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label>תפקיד</Label>
              <SearchableSelect value={editRole} onValueChange={(v) => setEditRole(v as UserRole)} className="mt-1.5"
                options={[
                  { value: "admin", label: "מנהל מערכת" },
                  { value: "project_manager", label: "מנהל פרויקט" },
                  { value: "referent", label: "רפרנט" },
                  { value: "viewer", label: "צפייה בלבד" },
                ]}
              />
            </div>
            {editRole === "project_manager" && (
              <div>
                <Label>פרויקטים משויכים</Label>
                <div className="space-y-2 mt-2 max-h-40 overflow-y-auto">
                  {projects
                    .filter((p) => {
                      const otherPM = users.find((u) => u.id !== editingUser?.id && u.role === "project_manager" && u.assignedProjects.includes(p.id));
                      return !otherPM || editProjects.includes(p.id);
                    })
                    .map((p) => (
                    <label key={p.id} className="flex items-center gap-2 cursor-pointer">
                      <Checkbox
                        checked={editProjects.includes(p.id)}
                        onCheckedChange={() => toggleEditProject(p.id)}
                      />
                      <span className="text-sm">{p.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            {editRole === "referent" && (
              <div>
                <Label>שלבים משויכים</Label>
                <div className="space-y-2 mt-2 max-h-40 overflow-y-auto">
                  {PROJECT_STAGES.map((stage, i) => (
                    <label key={i} className="flex items-center gap-2 cursor-pointer">
                      <Checkbox
                        checked={editStages.includes(i)}
                        onCheckedChange={() => setEditStages((prev) => prev.includes(i) ? prev.filter((id) => id !== i) : [...prev, i])}
                      />
                      <span className="text-sm">{stage}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <Button onClick={saveEditUser} className="w-full">שמור שינויים</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Add User Dialog */}
      <Dialog open={newUserDialogOpen} onOpenChange={setNewUserDialogOpen}>
        <DialogContent dir="rtl">
          <DialogHeader>
             <DialogTitle>משתמש חדש</DialogTitle>
             <DialogDescription>הוסף משתמש חדש למערכת</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם</Label>
              <Input value={newUserName} onChange={(e) => setNewUserName(e.target.value)} className="mt-1.5" placeholder="שם מלא" />
            </div>
            <div>
              <Label>אימייל</Label>
              <Input value={newUserEmail} onChange={(e) => setNewUserEmail(e.target.value)} className="mt-1.5" placeholder="email@example.com" />
            </div>
            <div>
              <Label>תפקיד</Label>
              <SearchableSelect value={newUserRole} onValueChange={(v) => setNewUserRole(v as UserRole)} className="mt-1.5"
                options={[
                  { value: "admin", label: "מנהל מערכת" },
                  { value: "project_manager", label: "מנהל פרויקט" },
                  { value: "referent", label: "רפרנט" },
                  { value: "viewer", label: "צפייה בלבד" },
                ]}
              />
            </div>
            {newUserRole === "project_manager" && (
              <div>
                <Label>פרויקטים משויכים</Label>
                <div className="space-y-2 mt-2 max-h-40 overflow-y-auto">
                  {projects
                    .filter((p) => {
                      const existingPM = users.find((u) => u.role === "project_manager" && u.assignedProjects.includes(p.id));
                      return !existingPM || newUserProjects.includes(p.id);
                    })
                    .map((p) => (
                    <label key={p.id} className="flex items-center gap-2 cursor-pointer">
                      <Checkbox
                        checked={newUserProjects.includes(p.id)}
                        onCheckedChange={() => toggleNewUserProject(p.id)}
                      />
                      <span className="text-sm">{p.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            {newUserRole === "referent" && (
              <div>
                <Label>שלבים משויכים</Label>
                <div className="space-y-2 mt-2 max-h-40 overflow-y-auto">
                  {PROJECT_STAGES.map((stage, i) => (
                    <label key={i} className="flex items-center gap-2 cursor-pointer">
                      <Checkbox
                        checked={newUserStages.includes(i)}
                        onCheckedChange={() => setNewUserStages((prev) => prev.includes(i) ? prev.filter((id) => id !== i) : [...prev, i])}
                      />
                      <span className="text-sm">{stage}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <Button onClick={handleAddUser} className="w-full">צור משתמש</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete User Confirmation */}
      <AlertDialog open={!!deleteUserId} onOpenChange={(open) => !open && setDeleteUserId(null)}>
        <AlertDialogContent dir="rtl">
          <AlertDialogHeader>
            <AlertDialogTitle>מחיקת משתמש</AlertDialogTitle>
            <AlertDialogDescription>
              האם אתה בטוח שברצונך למחוק את {users.find((u) => u.id === deleteUserId)?.name}? פעולה זו אינה ניתנת לביטול.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-row-reverse gap-2">
            <AlertDialogCancel>ביטול</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={async () => {
                if (deleteUserId) {
                  await deleteUser(deleteUserId);
                  toast({ title: "המשתמש נמחק ✓" });
                  setDeleteUserId(null);
                }
              }}
            >
              מחק
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Project Confirmation */}
      <AlertDialog open={!!deleteProjectId} onOpenChange={(open) => !open && setDeleteProjectId(null)}>
        <AlertDialogContent dir="rtl">
          <AlertDialogHeader>
            <AlertDialogTitle>מחיקת פרויקט</AlertDialogTitle>
            <AlertDialogDescription>
              האם אתה בטוח שברצונך למחוק את {projects.find((p) => p.id === deleteProjectId)?.name}? כל הלקחים המשויכים יימחקו גם כן. פעולה זו אינה ניתנת לביטול.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-row-reverse gap-2">
            <AlertDialogCancel>ביטול</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={async () => {
                if (deleteProjectId) {
                  await deleteProject(deleteProjectId);
                  toast({ title: "הפרויקט נמחק ✓" });
                  setDeleteProjectId(null);
                }
              }}
            >
              מחק
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={projectDialogOpen} onOpenChange={setProjectDialogOpen}>
        <DialogContent dir="rtl" className="max-w-md max-h-[85vh] overflow-y-auto">
          <DialogHeader>
             <DialogTitle>{editingProjectId !== null ? "עריכת פרויקט" : "פרויקט חדש"}</DialogTitle>
             <DialogDescription>{editingProjectId !== null ? "עדכן פרטי פרויקט" : "צור פרויקט חדש במערכת"}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם הפרויקט</Label>
              <Input value={projName} onChange={(e) => setProjName(e.target.value)} placeholder="הכנס שם פרויקט..." className="mt-1.5" />
            </div>
            <div>
              <Label>סוג פרויקט</Label>
              <SearchableSelect value={projType} onValueChange={(v) => setProjType(v as ProjectType)} className="mt-1.5"
                options={[
                  { value: "new_build", label: "הקמה" },
                  { value: "expansion", label: "הרחבה" },
                  { value: "maintenance", label: "שו\"ש" },
                ]}
              />
            </div>
            <div>
              <Label>סוג תחנה</Label>
              <SearchableSelect value={projStation} onValueChange={(v) => setProjStation(v as StationType)} className="mt-1.5"
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
                value={projManager}
                onValueChange={setProjManager}
                className="mt-1.5"
                options={users.map((u) => ({
                  value: u.id,
                  label: `${u.name} (${roleLabels[u.role]})`,
                }))}
              />
            </div>
            <div>
              <Label>רמת סיכון</Label>
              <SearchableSelect value={projRisk} onValueChange={(v) => setProjRisk(v as "high" | "medium" | "low")} className="mt-1.5"
                options={[
                  { value: "high", label: "סיכון גבוה" },
                  { value: "medium", label: "סיכון בינוני" },
                  { value: "low", label: "סיכון נמוך" },
                ]}
              />
            </div>
            {editingProjectId === null && (
              <div>
                <Label>שלב התחלתי</Label>
                <SearchableSelect value={String(projStage)} onValueChange={(v) => setProjStage(Number(v))} className="mt-1.5"
                  options={[
                    ...PROJECT_STAGES.map((s, i) => ({ value: String(i), label: s })),
                  ]}
                />
              </div>
            )}
            <div>
              <Label>אתר</Label>
              <SiteCombobox value={projSite} onValueChange={setProjSite} />
            </div>
            <div>
              <Label>ציוד / נושאים קשורים</Label>
              <div className="space-y-2 mt-2 max-h-40 overflow-y-auto">
                {equipment.map((eq) => (
                  <label key={eq.id} className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={projEquipment.includes(eq.id)}
                      onCheckedChange={() => toggleProjEquipment(eq.id)}
                    />
                    <span className="text-sm">{eq.name}</span>
                    <span className="text-xs text-muted-foreground">({eq.category})</span>
                  </label>
                ))}
              </div>
            </div>
            <Button onClick={saveProject} className="w-full" disabled={!projName.trim() || !projManager}>
              {editingProjectId !== null ? "שמור שינויים" : "צור פרויקט"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Rename Category Dialog */}
      <Dialog open={!!editingCategory} onOpenChange={(open) => !open && setEditingCategory(null)}>
        <DialogContent dir="rtl">
          <DialogHeader>
             <DialogTitle>שינוי שם קטגוריה</DialogTitle>
             <DialogDescription>הזן שם חדש לקטגוריה</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם חדש</Label>
              <Input value={editCategoryName} onChange={(e) => setEditCategoryName(e.target.value)} className="mt-1.5" />
            </div>
            <Button onClick={handleRenameCategory} className="w-full">שמור</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Category Confirmation */}
      <AlertDialog open={!!deleteCategoryName} onOpenChange={(open) => !open && setDeleteCategoryName(null)}>
        <AlertDialogContent dir="rtl">
          <AlertDialogHeader>
            <AlertDialogTitle>מחיקת קטגוריה</AlertDialogTitle>
            <AlertDialogDescription>
              האם אתה בטוח שברצונך למחוק את הקטגוריה "{deleteCategoryName}"? כל פריטי הציוד בקטגוריה זו יימחקו. פעולה זו אינה ניתנת לביטול.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-row-reverse gap-2">
            <AlertDialogCancel>ביטול</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleDeleteCategory}
            >
              מחק
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Rename Lesson Category Dialog */}
      <Dialog open={!!editingLessonCategory} onOpenChange={(open) => !open && setEditingLessonCategory(null)}>
        <DialogContent dir="rtl">
          <DialogHeader>
             <DialogTitle>שינוי שם קטגוריית לקחים</DialogTitle>
             <DialogDescription>הזן שם חדש לקטגוריה</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם חדש</Label>
              <Input value={editLessonCategoryName} onChange={(e) => setEditLessonCategoryName(e.target.value)} className="mt-1.5" />
            </div>
            <Button onClick={handleRenameLessonCategory} className="w-full">שמור</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Lesson Category Confirmation */}
      <AlertDialog open={!!deleteLessonCategoryName} onOpenChange={(open) => !open && setDeleteLessonCategoryName(null)}>
        <AlertDialogContent dir="rtl">
          <AlertDialogHeader>
            <AlertDialogTitle>מחיקת קטגוריית לקחים</AlertDialogTitle>
            <AlertDialogDescription>
              האם אתה בטוח שברצונך למחוק את הקטגוריה "{deleteLessonCategoryName}"?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-row-reverse gap-2">
            <AlertDialogCancel>ביטול</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleDeleteLessonCategory}
            >
              מחק
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Feedback Admin Notes Dialog */}
      <Dialog open={!!feedbackNotesId} onOpenChange={(open) => !open && setFeedbackNotesId(null)}>
        <DialogContent dir="rtl">
          <DialogHeader>
             <DialogTitle>הערת מנהל לפידבק</DialogTitle>
             <DialogDescription>הוסף הערה או עדכון לפידבק</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>הערה</Label>
              <Textarea
                value={feedbackNotes}
                onChange={(e) => setFeedbackNotes(e.target.value)}
                placeholder="הוסף הערה או תגובה..."
                rows={3}
                className="mt-1.5"
              />
            </div>
            <Button
              onClick={async () => {
                if (feedbackNotesId) {
                  await updateFeedbackStatus(feedbackNotesId, feedbacks.find((f) => f.id === feedbackNotesId)?.status || "open", feedbackNotes);
                  toast({ title: "הערה עודכנה ✓" });
                  setFeedbackNotesId(null);
                }
              }}
              className="w-full"
            >
              שמור הערה
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Feedback Confirmation */}
      <AlertDialog open={!!deleteFeedbackId} onOpenChange={(open) => !open && setDeleteFeedbackId(null)}>
        <AlertDialogContent dir="rtl">
          <AlertDialogHeader>
            <AlertDialogTitle>מחיקת פידבק</AlertDialogTitle>
            <AlertDialogDescription>האם אתה בטוח שברצונך למחוק פידבק זה? פעולה זו אינה ניתנת לביטול.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-row-reverse gap-2">
            <AlertDialogCancel>ביטול</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={async () => {
                if (deleteFeedbackId) {
                  await deleteFeedback(deleteFeedbackId);
                  toast({ title: "פידבק נמחק ✓" });
                  setDeleteFeedbackId(null);
                }
              }}
            >
              מחק
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default Admin;
