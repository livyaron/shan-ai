import { useEffect, useMemo, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { ScrollArea } from "@/components/ui/scroll-area";
import { FileDown, FileText, Loader2 } from "lucide-react";
import { toast } from "@/hooks/use-toast";
import { computeAllLessonQuality } from "@/lib/lessonQuality";
import {
  RecLesson,
  RecProject,
  ScoredLesson,
  selectRecommendedLessons,
  exportRecommendedLessonsToDOCX,
  exportRecommendedLessonsToPDF,
  QualityInfo,
} from "@/lib/recommendedLessonsExport";
import type { Lesson, LessonImplementation, Project } from "@/context/UserContext";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: Project;
  /** All lessons in the system. */
  allLessons: Lesson[];
  /** All implementations in the system (for quality scores). */
  implementations: LessonImplementation[];
  /** All project stage names in chronological order (full list). */
  stages: string[];
  /** Active stage names of this project — used as default selection. */
  defaultStages: string[];
  /** Map of projectId → projectType / stationType so we can boost match. */
  projectMetaById: Map<number, { projectType: string; stationType: string }>;
}

const formatHebrewDate = (d: Date) =>
  d.toLocaleDateString("he-IL", { day: "2-digit", month: "2-digit", year: "numeric" });

export default function RecommendedLessonsDialog({
  open, onOpenChange, project, allLessons, implementations, stages, defaultStages, projectMetaById,
}: Props) {
  const [deselected, setDeselected] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState<"docx" | "pdf" | null>(null);
  const [selectedStages, setSelectedStages] = useState<string[]>(defaultStages);

  // Chronologically-ordered subset of selected stages, based on full `stages` order.
  const orderedSelectedStages = useMemo(
    () => stages.filter((s) => selectedStages.includes(s)),
    [stages, selectedStages],
  );

  const recProject: RecProject = useMemo(() => ({
    name: project.name,
    projectType: project.projectType,
    stationType: project.stationType,
    site: project.site,
    equipmentIds: project.equipmentIds,
    stages: orderedSelectedStages,
  }), [project, orderedSelectedStages]);

  const recLessons: RecLesson[] = useMemo(() =>
    allLessons.map((l) => {
      const meta = l.projectId ? projectMetaById.get(l.projectId) : undefined;
      return {
        id: l.id,
        title: l.title,
        description: l.description,
        recommendation: l.recommendation,
        stage: l.stage,
        category: l.category,
        professionalDomain: l.professionalDomain,
        risk: l.risk,
        equipmentIds: l.equipmentIds,
        status: l.status,
        workflowStatus: l.workflowStatus,
        projectId: l.projectId,
        eventDate: l.eventDate,
        date: l.date,
        sourceProjectType: meta?.projectType,
        sourceStationType: meta?.stationType,
      };
    }),
  [allLessons, projectMetaById]);

  const qualityById: Map<number, QualityInfo> = useMemo(() => {
    const m = computeAllLessonQuality(allLessons.map((l) => l.id), implementations);
    const out = new Map<number, QualityInfo>();
    m.forEach((v, k) => out.set(k, {
      shrunk: v.qualityScoreShrunk,
      avgScore: v.avgScore,
      implementationRate: v.implementationRate,
      responses: v.respondedCount,
    }));
    return out;
  }, [allLessons, implementations]);

  const selected = useMemo(
    () => selectRecommendedLessons(recProject, recLessons, qualityById, project.id),
    [recProject, recLessons, qualityById, project.id],
  );

  // Reset deselection + restore default stage selection whenever dialog reopens
  useEffect(() => {
    if (open) {
      setDeselected(new Set());
      setSelectedStages(defaultStages.length > 0 ? defaultStages : stages);
    }
  }, [open, defaultStages, stages]);

  const totalAuto = useMemo(() => selected.reduce((s, g) => s + g.lessons.length, 0), [selected]);
  const totalKept = useMemo(
    () => selected.reduce((s, g) => s + g.lessons.filter((l) => !deselected.has(l.id)).length, 0),
    [selected, deselected],
  );

  const toggle = (id: number) => {
    setDeselected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const buildPayload = () => {
    const stagesPayload = selected.map((g) => ({
      stageName: g.stageName,
      lessons: g.lessons.filter((l) => !deselected.has(l.id)) as ScoredLesson[],
    }));
    return {
      project: recProject,
      generatedAt: formatHebrewDate(new Date()),
      stages: stagesPayload,
      totalSelected: stagesPayload.reduce((s, g) => s + g.lessons.length, 0),
    };
  };

  const handleDocx = async () => {
    setExporting("docx");
    try {
      await exportRecommendedLessonsToDOCX(buildPayload(), `לקחים-מומלצים-${project.name}.docx`);
      toast({ title: "המסמך הורד בהצלחה" });
    } catch (e) {
      toast({ title: "שגיאה ביצירת DOCX", description: String(e), variant: "destructive" });
    } finally { setExporting(null); }
  };

  const handlePdf = async () => {
    setExporting("pdf");
    try {
      exportRecommendedLessonsToPDF(buildPayload(), `recommended-lessons-${project.id}.pdf`);
      toast({ title: "המסמך הורד בהצלחה" });
    } catch (e) {
      toast({ title: "שגיאה ביצירת PDF", description: String(e), variant: "destructive" });
    } finally { setExporting(null); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent dir="rtl" className="max-w-3xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>מסמך לקחים מומלצים — {project.name}</DialogTitle>
          <DialogDescription>
            המערכת בחרה אוטומטית עד 10 לקחים מומלצים לכל שלב, לפי התאמה לפרויקט ואיכות הלקח.
            ניתן להסיר סימון של לקחים שאינם רלוונטיים. לא ניתן להוסיף ידנית לקחים נוספים בשלב זה.
          </DialogDescription>
        </DialogHeader>

        {/* Stage selection */}
        <div className="border rounded-md p-3 space-y-2 bg-muted/30">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium">
              שלבים לכלול במסמך
              <Badge variant="secondary" className="mx-2">
                {selectedStages.length} / {stages.length}
              </Badge>
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() =>
                  setSelectedStages(selectedStages.length === stages.length ? defaultStages : stages)
                }
              >
                {selectedStages.length === stages.length ? "חזרה לשלבים פעילים" : "בחר הכל"}
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 max-h-32 overflow-y-auto">
            {stages.map((s) => {
              const checked = selectedStages.includes(s);
              const isLast = checked && selectedStages.length === 1;
              return (
                <label
                  key={s}
                  className={`flex items-center gap-2 text-sm ${isLast ? "opacity-70 cursor-not-allowed" : "cursor-pointer"}`}
                  title={isLast ? "חובה לבחור לפחות שלב אחד" : undefined}
                >
                  <Checkbox
                    checked={checked}
                    disabled={isLast}
                    onCheckedChange={(v) => {
                      if (v) setSelectedStages((prev) => [...prev, s]);
                      else setSelectedStages((prev) => prev.filter((x) => x !== s));
                    }}
                  />
                  <span>{s}</span>
                </label>
              );
            })}
          </div>
          <p className="text-[11px] text-muted-foreground">
            ברירת המחדל: השלב/ים הפעילים של הפרויקט. ניתן להרחיב לכלל שלבי הפרויקט. השלבים יופיעו לפי הסדר הכרונולוגי.
          </p>
        </div>

        <div className="flex flex-wrap gap-2 text-sm text-muted-foreground border-y py-2">
          <span>סוג פרויקט: <b>{project.projectType}</b></span>
          <span>·</span>
          <span>סוג תחנה: <b>{project.stationType}</b></span>
          {project.site && (<><span>·</span><span>אתר: <b>{project.site}</b></span></>)}
          <span>·</span>
          <span>נבחרו אוטומטית: <b>{totalAuto}</b></span>
          <span>·</span>
          <span>ייכללו במסמך: <b>{totalKept}</b></span>
        </div>

        <ScrollArea className="flex-1 -mx-6 px-6">
          {selected.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              לא נמצאו לקחים מומלצים בהתאם למאפייני הפרויקט. ניתן עדיין להפיק את המסמך — הוא יכלול שער ותקציר בלבד.
            </div>
          ) : (
            <Accordion type="multiple" defaultValue={selected.map((s) => s.stageName)} className="w-full">
              {selected.map((stage) => (
                <AccordionItem key={stage.stageName} value={stage.stageName}>
                  <AccordionTrigger className="text-right">
                    <span className="flex gap-2 items-center">
                      {stage.stageName}
                      <Badge variant="secondary">{stage.lessons.length} לקחים</Badge>
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    {stage.lessons.length === 0 ? (
                      <div className="text-sm text-muted-foreground py-2">אין לקחים מומלצים לשלב זה כרגע</div>
                    ) : (
                      <div className="space-y-2">
                        {stage.lessons.map((l) => {
                          const checked = !deselected.has(l.id);
                          return (
                            <div key={l.id} className="flex items-start gap-3 p-2 rounded border bg-card">
                              <Checkbox checked={checked} onCheckedChange={() => toggle(l.id)} className="mt-1" />
                              <div className="flex-1 min-w-0">
                                <div className="font-medium">{l.title}</div>
                                <div className="text-sm text-muted-foreground line-clamp-2">{l.description}</div>
                                <div className="flex flex-wrap gap-1 mt-1 text-xs">
                                  {l.category && <Badge variant="outline">{l.category}</Badge>}
                                  {l.professionalDomain && <Badge variant="outline">{l.professionalDomain}</Badge>}
                                  <Badge variant="outline">שכבה {l.selectionLayer}</Badge>
                                  <Badge variant="secondary">ניקוד התאמה: {l.finalScore.toFixed(0)}</Badge>
                                  <Badge variant="secondary">איכות: {l.quality?.shrunk.toFixed(2) ?? "—"}/2</Badge>
                                  <Badge variant="secondary">
                                    יישום: {l.quality?.implementationRate != null ? `${Math.round(l.quality.implementationRate * 100)}%` : "—"}
                                  </Badge>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          )}
        </ScrollArea>

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>ביטול</Button>
          <Button variant="outline" onClick={handlePdf} disabled={!!exporting} className="gap-1.5">
            {exporting === "pdf" ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
            הורד PDF
          </Button>
          <Button onClick={handleDocx} disabled={!!exporting} className="gap-1.5">
            {exporting === "docx" ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileDown className="w-4 h-4" />}
            הורד מסמך (DOCX)
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
