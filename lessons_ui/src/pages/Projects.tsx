import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { FolderKanban, BookOpen, ChevronLeft, Eye, EyeOff, Filter, Search, ArrowUpAZ, ArrowDownAZ, Zap } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUser, PROJECT_STAGES, projectTypeLabels, stationTypeLabels } from "@/context/UserContext";
import AddProjectDialog from "@/components/AddProjectDialog";
import { riskColors, riskLabels } from "@/lib/constants";
import { useActionRequired } from "@/hooks/useActionRequired";
import { supabase } from "@/integrations/supabase/client";
import SearchableSelect from "@/components/ui/searchable-select";

interface SiteOption {
  label: string;
  value: string;
}

const Projects = () => {
  const navigate = useNavigate();
  const { currentUser, projects, users, lessons, implementations } = useUser();
  const { projectNeedsAction } = useActionRequired(lessons, implementations, projects, currentUser);

  const [showAll, setShowAll] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [riskFilter, setRiskFilter] = useState<string>("all");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [stationFilter, setStationFilter] = useState<string>("all");
  const [stageFilter, setStageFilter] = useState<string>("all");
  const [siteFilter, setSiteFilter] = useState<string>("all");
  const [sortOrder, setSortOrder] = useState<string>("asc");
  const [siteOptions, setSiteOptions] = useState<SiteOption[]>([]);

  // Load site options from managed form_field_options
  useEffect(() => {
    const loadSiteOptions = async () => {
      const { data: configs } = await supabase
        .from("form_field_configs")
        .select("id")
        .eq("form_type", "project")
        .eq("field_key", "site")
        .limit(1);

      if (!configs || configs.length === 0) return;

      const { data: opts } = await supabase
        .from("form_field_options")
        .select("label, value")
        .eq("field_config_id", configs[0].id)
        .eq("is_active", true)
        .order("sort_order");

      if (opts) {
        setSiteOptions(opts.map((o) => ({ label: o.label, value: o.value })));
      }
    };
    loadSiteOptions();
  }, []);

  const isAdmin = currentUser.role === "admin";
  const isViewer = currentUser.role === "viewer";
  const baseProjects = isAdmin || isViewer || showAll
    ? projects
    : projects.filter((p) => currentUser.assignedProjects.includes(p.id) || p.managerId === currentUser.id);

  const filteredProjects = useMemo(() => {
    const trimmedSearch = searchTerm.trim().toLowerCase();

    return baseProjects
      .filter((p) => {
        // Search filter — name and site
        if (trimmedSearch) {
          const nameMatch = p.name.toLowerCase().includes(trimmedSearch);
          const siteMatch = p.site?.toLowerCase().includes(trimmedSearch) ?? false;
          if (!nameMatch && !siteMatch) return false;
        }
        if (riskFilter !== "all" && p.risk !== riskFilter) return false;
        if (typeFilter !== "all" && p.projectType !== typeFilter) return false;
        if (stationFilter !== "all" && p.stationType !== stationFilter) return false;
        if (stageFilter !== "all") {
          const activeStages = p.stageIndexes && p.stageIndexes.length > 0 ? p.stageIndexes : [p.stageIndex];
          if (!activeStages.includes(Number(stageFilter))) return false;
        }
        if (siteFilter !== "all" && p.site !== siteFilter) return false;
        return true;
      })
      .sort((a, b) => {
        // Primary sort: action-required first
        const aAction = projectNeedsAction(a.id).needed ? 1 : 0;
        const bAction = projectNeedsAction(b.id).needed ? 1 : 0;
        if (bAction !== aAction) return bAction - aAction;
        // Secondary sort: alphabetical by name
        const direction = sortOrder === "asc" ? 1 : -1;
        return direction * a.name.localeCompare(b.name, "he");
      });
  }, [baseProjects, searchTerm, riskFilter, typeFilter, stationFilter, stageFilter, siteFilter, sortOrder, projectNeedsAction]);

  const hasActiveFilters = searchTerm.trim() !== "" || riskFilter !== "all" || typeFilter !== "all" || stationFilter !== "all" || stageFilter !== "all" || siteFilter !== "all";

  const clearFilters = () => {
    setSearchTerm("");
    setRiskFilter("all");
    setTypeFilter("all");
    setStationFilter("all");
    setStageFilter("all");
    setSiteFilter("all");
  };

  const getManagerName = (managerId: string) => {
    const user = users.find((u) => u.id === managerId);
    return user?.name || "";
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-bold">פרויקטים</h1>
          <p className="text-muted-foreground mt-1">
            {isAdmin
              ? "כל הפרויקטים בארגון"
              : showAll
                ? `מציג את כל ${baseProjects.length} הפרויקטים`
                : `${baseProjects.length} פרויקטים שהוקצו לך`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!isAdmin && !isViewer && (
            <Button
              variant={showAll ? "default" : "outline"}
              size="sm"
              onClick={() => setShowAll(!showAll)}
              className="gap-1.5"
            >
              {showAll ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              {showAll ? "הפרויקטים שלי" : "כל הפרויקטים"}
            </Button>
          )}
          {(isAdmin || currentUser.role === "project_manager") && <AddProjectDialog />}
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-[200px]">
          <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
          <Input
            placeholder="חיפוש פרויקט..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="h-9 text-sm pr-9"
          />
        </div>

        <Filter className="w-4 h-4 text-muted-foreground" />

        <SearchableSelect value={riskFilter} onValueChange={setRiskFilter} className="w-[140px] h-9 text-sm" placeholder="סיכון"
          options={[
            { value: "all", label: "כל הסיכונים" },
            { value: "high", label: "גבוה" },
            { value: "medium", label: "בינוני" },
            { value: "low", label: "נמוך" },
          ]}
        />

        <SearchableSelect value={typeFilter} onValueChange={setTypeFilter} className="w-[140px] h-9 text-sm" placeholder="סוג פרויקט"
          options={[
            { value: "all", label: "כל הסוגים" },
            { value: "new_build", label: "הקמה" },
            { value: "expansion", label: "הרחבה" },
            { value: "maintenance", label: "שו&quot;ש" },
          ]}
        />

        <SearchableSelect value={stationFilter} onValueChange={setStationFilter} className="w-[150px] h-9 text-sm" placeholder="סוג תחנה"
          options={[
            { value: "all", label: "כל התחנות" },
            { value: "closed", label: "תחנה סגורה" },
            { value: "open", label: "תחנה פתוחה" },
            { value: "switching", label: "תחנת מיתוג" },
            { value: "combined", label: "תחנה משולבת" },
            { value: "mobile_substation", label: "תחנת משנה ניידת" },
          ]}
        />

        <SearchableSelect value={stageFilter} onValueChange={setStageFilter} className="w-[160px] h-9 text-sm" placeholder="שלב"
          options={[
            { value: "all", label: "כל השלבים" },
            ...PROJECT_STAGES.map((stage, idx) => ({ value: String(idx), label: stage })),
          ]}
        />

        <SearchableSelect value={siteFilter} onValueChange={setSiteFilter} className="w-[160px] h-9 text-sm" placeholder="אתר"
          options={[
            { value: "all", label: "כל האתרים" },
            ...siteOptions.map((opt) => ({ value: String(opt.value), label: opt.label })),
          ]}
        />

        <SearchableSelect value={sortOrder} onValueChange={setSortOrder} className="w-[130px] h-9 text-sm"
          options={[
            { value: "asc", label: "מיון: א-ת" },
            { value: "desc", label: "מיון: ת-א" },
          ]}
        />

        {hasActiveFilters && (
          <Button variant="ghost" size="sm" onClick={clearFilters} className="text-muted-foreground">
            נקה מסננים
          </Button>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {filteredProjects.map((project) => {
          const activeStages = project.stageIndexes && project.stageIndexes.length > 0 ? project.stageIndexes : [project.stageIndex];
          const sortedActive = [...activeStages].sort((a, b) => a - b);
          const maxActive = Math.max(...sortedActive);
          const progress = ((maxActive + 1) / PROJECT_STAGES.length) * 100;
          const isOwnProject = project.managerId === currentUser.id;
          const visibleStages = sortedActive.slice(0, 2);
          const hiddenStages = sortedActive.slice(2);

          return (
            <Card
              key={project.id}
              className={`shadow-sm hover:shadow-lg transition-all duration-300 cursor-pointer group ${
                projectNeedsAction(project.id).needed
                  ? "border-r-4 border-r-warning border-t-0 border-b-0 border-l-0 bg-warning/[0.03] ring-1 ring-warning/20"
                  : "border-0"
              } ${!isAdmin && showAll && !isOwnProject ? "opacity-75" : ""}`}
              onClick={() => navigate(`/projects/${project.id}`)}
            >
              <CardContent className="p-6">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-11 h-11 rounded-xl bg-primary flex items-center justify-center">
                      <FolderKanban className="w-5 h-5 text-primary-foreground" />
                    </div>
                    <div className="min-w-0">
                      <h3 className="font-heading font-semibold text-lg">{project.name}</h3>
                      <div className="flex items-center gap-1.5 flex-wrap mt-1">
                        {visibleStages.map((si) => (
                          <Badge key={si} variant="secondary" className="bg-accent/10 text-accent border-accent/20 text-[11px] py-0 px-1.5">
                            {PROJECT_STAGES[si]}
                          </Badge>
                        ))}
                        {hiddenStages.length > 0 && (
                          <Badge
                            variant="secondary"
                            className="bg-muted text-muted-foreground text-[11px] py-0 px-1.5"
                            title={hiddenStages.map((si) => PROJECT_STAGES[si]).join(", ")}
                          >
                            +{hiddenStages.length}
                          </Badge>
                        )}
                        <span className="text-xs text-muted-foreground">· {projectTypeLabels[project.projectType]} · {stationTypeLabels[project.stationType]}</span>
                      </div>
                    </div>
                  </div>
                  <ChevronLeft className="w-5 h-5 text-muted-foreground group-hover:text-accent transition-colors" />
                </div>

                <div className="mb-4">
                  <Progress value={progress} className="h-2" />
                  <p className="text-xs text-muted-foreground mt-1.5">
                    שלב {maxActive + 1} מתוך {PROJECT_STAGES.length}
                  </p>
                </div>

                <div className="flex items-center gap-4 text-sm text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <BookOpen className="w-4 h-4" />
                    {project.lessonsCount} לקחים
                  </span>
                    <Badge variant="outline" className={riskColors[project.risk]}>
                    {riskLabels[project.risk]}
                  </Badge>
                  {projectNeedsAction(project.id).needed && (
                    <Badge variant="outline" className="text-[11px] py-0.5 px-2 bg-warning/15 text-warning border-warning/30 animate-pulse font-bold">
                      <Zap className="w-3.5 h-3.5 ml-1" />
                      {projectNeedsAction(project.id).label}
                    </Badge>
                  )}
                  {!isAdmin && showAll && !isOwnProject && (
                    <span className="text-xs text-muted-foreground/70">
                      מנהל: {getManagerName(project.managerId)}
                    </span>
                  )}
                </div>
              </CardContent>
            </Card>
          );
        })}
        {filteredProjects.length === 0 && (
          <p className="text-muted-foreground col-span-2 text-center py-12">
            {hasActiveFilters ? "לא נמצאו פרויקטים התואמים את המסננים" : "לא הוקצו לך פרויקטים"}
          </p>
        )}
      </div>
    </div>
  );
};

export default Projects;
