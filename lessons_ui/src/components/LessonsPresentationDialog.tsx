import { useMemo, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChevronLeft, ChevronRight, Download, FileText, Eye, ArrowLeft } from "lucide-react";
import { toast } from "@/hooks/use-toast";
import {
  buildSummary,
  exportPresentationToPDF,
  exportPresentationToPPTX,
  groupLessonsByStage,
  PresentationData,
  PresentationLesson,
} from "@/lib/presentationExport";

const MAX_PER_STAGE = 5;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string;
  /** Ordered stage names active in the project (chronological). */
  activeStages: string[];
  /** Approved/distributed lessons of the project (already filtered). */
  lessons: PresentationLesson[];
}

type Mode = "select" | "preview";

export default function LessonsPresentationDialog({
  open,
  onOpenChange,
  projectName,
  activeStages,
  lessons,
}: Props) {
  const [mode, setMode] = useState<Mode>("select");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [slideIndex, setSlideIndex] = useState(0);

  const grouped = useMemo(
    () => groupLessonsByStage(lessons, activeStages),
    [lessons, activeStages]
  );

  const selectedByStage = useMemo(() => {
    return grouped.map((g) => ({
      stageName: g.stageName,
      lessons: g.lessons.filter((l) => selectedIds.has(l.id)).slice(0, MAX_PER_STAGE),
    })).filter((g) => g.lessons.length > 0);
  }, [grouped, selectedIds]);

  const totalSelected = selectedByStage.reduce((sum, g) => sum + g.lessons.length, 0);

  const toggleLesson = (lessonId: number, stageName: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(lessonId)) {
        next.delete(lessonId);
        return next;
      }
      // enforce 5-per-stage
      const stageGroup = grouped.find((g) => g.stageName === stageName);
      if (stageGroup) {
        const currentSelectedInStage = stageGroup.lessons.filter((l) => next.has(l.id)).length;
        if (currentSelectedInStage >= MAX_PER_STAGE) {
          toast({
            title: "הגעת למקסימום",
            description: `ניתן לבחור עד ${MAX_PER_STAGE} לקחים לכל שלב.`,
            variant: "destructive",
          });
          return prev;
        }
      }
      next.add(lessonId);
      return next;
    });
  };

  const selectAllInStage = (stageName: string, checked: boolean) => {
    const group = grouped.find((g) => g.stageName === stageName);
    if (!group) return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (!checked) {
        group.lessons.forEach((l) => next.delete(l.id));
      } else {
        group.lessons.slice(0, MAX_PER_STAGE).forEach((l) => next.add(l.id));
        if (group.lessons.length > MAX_PER_STAGE) {
          toast({
            title: "נבחרו 5 הראשונים",
            description: `שלב "${stageName}" מוגבל ל-${MAX_PER_STAGE} לקחים.`,
          });
        }
      }
      return next;
    });
  };

  const buildData = (): PresentationData => ({
    projectName,
    generatedAt: new Date().toLocaleDateString("he-IL"),
    stages: selectedByStage,
  });

  const handleExportPPTX = async () => {
    if (totalSelected === 0) {
      toast({ title: "לא נבחרו לקחים", variant: "destructive" });
      return;
    }
    try {
      const data = buildData();
      const safeName = projectName.replace(/[^\p{L}\p{N}_-]+/gu, "_");
      await exportPresentationToPPTX(data, `לקחים-${safeName}.pptx`);
      toast({ title: "המצגת הורדה ✓" });
    } catch (e) {
      console.error(e);
      toast({ title: "שגיאה בהורדת PPTX", variant: "destructive" });
    }
  };

  const handleExportPDF = () => {
    if (totalSelected === 0) {
      toast({ title: "לא נבחרו לקחים", variant: "destructive" });
      return;
    }
    try {
      const data = buildData();
      const safeName = projectName.replace(/[^\p{L}\p{N}_-]+/gu, "_");
      exportPresentationToPDF(data, `לקחים-${safeName}.pdf`);
      toast({ title: "הקובץ הורד ✓" });
    } catch (e) {
      console.error(e);
      toast({ title: "שגיאה בהורדת PDF", variant: "destructive" });
    }
  };

  // Build flat slide list for preview
  const slides = useMemo(() => {
    const list: { type: "cover" | "stage" | "lesson"; stageName?: string; lesson?: PresentationLesson; count?: number }[] = [];
    list.push({ type: "cover" });
    for (const g of selectedByStage) {
      list.push({ type: "stage", stageName: g.stageName, count: g.lessons.length });
      for (const l of g.lessons) {
        list.push({ type: "lesson", stageName: g.stageName, lesson: l });
      }
    }
    return list;
  }, [selectedByStage]);

  const enterPreview = () => {
    if (totalSelected === 0) {
      toast({ title: "בחר לפחות לקח אחד להצגה", variant: "destructive" });
      return;
    }
    setSlideIndex(0);
    setMode("preview");
  };

  const handleClose = (next: boolean) => {
    if (!next) {
      setMode("select");
      setSlideIndex(0);
    }
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent dir="rtl" className="max-w-5xl max-h-[90vh] overflow-hidden p-0">
        {mode === "select" ? (
          <div className="flex flex-col max-h-[90vh]">
            <DialogHeader className="p-6 pb-3 border-b">
              <DialogTitle>מצגת לקחי פרויקט</DialogTitle>
              <DialogDescription>
                בחר עד {MAX_PER_STAGE} לקחים לכל שלב. הלקחים יוצגו לפי סדר השלבים בפרויקט.
              </DialogDescription>
            </DialogHeader>

            <ScrollArea className="flex-1 p-6">
              {grouped.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">אין לקחים זמינים בפרויקט.</p>
              ) : (
                <Accordion type="multiple" defaultValue={grouped.map((g) => g.stageName)} className="space-y-2">
                  {grouped.map((group) => {
                    const selectedInStage = group.lessons.filter((l) => selectedIds.has(l.id)).length;
                    const allChecked = selectedInStage > 0 && selectedInStage === Math.min(group.lessons.length, MAX_PER_STAGE);
                    return (
                      <AccordionItem key={group.stageName} value={group.stageName} className="border rounded-lg px-3">
                        <div className="flex items-center justify-between gap-3">
                          <AccordionTrigger className="hover:no-underline flex-1">
                            <div className="flex items-center gap-2">
                              <span className="font-medium">{group.stageName}</span>
                              <Badge variant="secondary">{group.lessons.length}</Badge>
                              {selectedInStage > 0 && (
                                <Badge variant="outline" className="bg-accent/10 text-accent border-accent/20">
                                  נבחרו {selectedInStage}/{MAX_PER_STAGE}
                                </Badge>
                              )}
                            </div>
                          </AccordionTrigger>
                          <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer ml-2">
                            <Checkbox
                              checked={allChecked}
                              onCheckedChange={(v) => selectAllInStage(group.stageName, !!v)}
                              onClick={(e) => e.stopPropagation()}
                            />
                            בחר הכל
                          </label>
                        </div>
                        <AccordionContent>
                          <div className="space-y-2 pt-2">
                            {group.lessons.map((lesson) => {
                              const checked = selectedIds.has(lesson.id);
                              return (
                                <label
                                  key={lesson.id}
                                  className="flex items-start gap-3 p-3 rounded-md border bg-card hover:bg-muted/50 cursor-pointer"
                                >
                                  <Checkbox
                                    checked={checked}
                                    onCheckedChange={() => toggleLesson(lesson.id, group.stageName)}
                                  />
                                  <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">{lesson.title}</p>
                                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                                      {buildSummary(lesson.description, 140)}
                                    </p>
                                    <p className="text-[11px] text-muted-foreground mt-1">
                                      {lesson.category && <>{lesson.category} · </>}
                                      {lesson.eventDate ? `אירוע: ${lesson.eventDate}` : lesson.date ? `תאריך: ${lesson.date}` : ""}
                                    </p>
                                  </div>
                                </label>
                              );
                            })}
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    );
                  })}
                </Accordion>
              )}
            </ScrollArea>

            <div className="border-t p-4 flex flex-wrap items-center justify-between gap-2 bg-muted/30">
              <span className="text-sm text-muted-foreground">
                סה"כ נבחרו: <strong>{totalSelected}</strong> לקחים ב-{selectedByStage.length} שלבים
              </span>
              <div className="flex flex-wrap gap-2">
                <Button variant="ghost" onClick={() => handleClose(false)}>ביטול</Button>
                <Button variant="outline" onClick={enterPreview} className="gap-1.5">
                  <Eye className="w-4 h-4" />
                  הצג מצגת
                </Button>
                <Button variant="outline" onClick={handleExportPDF} className="gap-1.5">
                  <FileText className="w-4 h-4" />
                  הורד PDF
                </Button>
                <Button onClick={handleExportPPTX} className="gap-1.5">
                  <Download className="w-4 h-4" />
                  הורד מצגת (PPTX)
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <PreviewMode
            slides={slides}
            slideIndex={slideIndex}
            setSlideIndex={setSlideIndex}
            projectName={projectName}
            onBack={() => setMode("select")}
            onExportPPTX={handleExportPPTX}
            onExportPDF={handleExportPDF}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function PreviewMode({
  slides,
  slideIndex,
  setSlideIndex,
  projectName,
  onBack,
  onExportPPTX,
  onExportPDF,
}: {
  slides: { type: "cover" | "stage" | "lesson"; stageName?: string; lesson?: PresentationLesson; count?: number }[];
  slideIndex: number;
  setSlideIndex: (n: number) => void;
  projectName: string;
  onBack: () => void;
  onExportPPTX: () => void;
  onExportPDF: () => void;
}) {
  const slide = slides[slideIndex];
  const total = slides.length;

  return (
    <div className="flex flex-col h-[90vh]">
      <DialogHeader className="p-3 pb-2 border-b flex-row items-center justify-between gap-2 space-y-0">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack} className="gap-1">
            <ArrowLeft className="w-4 h-4 rotate-180" />
            חזרה לבחירה
          </Button>
          <DialogTitle className="text-sm">תצוגה מקדימה — {projectName}</DialogTitle>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onExportPDF} className="gap-1.5">
            <FileText className="w-4 h-4" />
            PDF
          </Button>
          <Button size="sm" onClick={onExportPPTX} className="gap-1.5">
            <Download className="w-4 h-4" />
            הורד PPTX
          </Button>
        </div>
      </DialogHeader>

      <div className="flex-1 flex items-center justify-center bg-muted/40 p-6 overflow-hidden">
        <div
          className="bg-background border shadow-lg rounded-md w-full max-w-4xl aspect-video p-10 flex flex-col"
          dir="rtl"
        >
          {slide?.type === "cover" && (
            <div className="flex-1 flex flex-col items-center justify-center text-center">
              <p className="text-sm text-muted-foreground">לקחי פרויקט</p>
              <h2 className="text-4xl font-bold mt-3">{projectName}</h2>
              <p className="text-xs text-muted-foreground mt-6">
                הופק: {new Date().toLocaleDateString("he-IL")}
              </p>
            </div>
          )}
          {slide?.type === "stage" && (
            <div className="flex-1 flex flex-col items-center justify-center text-center">
              <h2 className="text-3xl font-bold text-primary">{slide.stageName}</h2>
              <p className="text-sm text-muted-foreground mt-3">{slide.count} לקחים</p>
            </div>
          )}
          {slide?.type === "lesson" && slide.lesson && (
            <>
              <p className="text-xs text-muted-foreground">{slide.stageName}</p>
              <h3 className="text-2xl font-bold mt-2 text-primary">{slide.lesson.title}</h3>
              <div className="mt-4">
                <p className="text-sm font-semibold">תיאור</p>
                <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                  {buildSummary(slide.lesson.description)}
                </p>
              </div>
              {slide.lesson.recommendation && (
                <div className="mt-4">
                  <p className="text-sm font-semibold">המלצה</p>
                  <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                    {buildSummary(slide.lesson.recommendation)}
                  </p>
                </div>
              )}
              <div className="mt-auto pt-4 text-[11px] text-muted-foreground">
                {[
                  slide.lesson.category,
                  slide.lesson.eventDate ? `אירוע: ${slide.lesson.eventDate}` : slide.lesson.date ? `תאריך: ${slide.lesson.date}` : null,
                ].filter(Boolean).join(" · ")}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="border-t p-3 flex items-center justify-between gap-2 bg-muted/30">
        <Button
          variant="outline"
          size="sm"
          onClick={() => setSlideIndex(Math.max(0, slideIndex - 1))}
          disabled={slideIndex === 0}
          className="gap-1"
        >
          <ChevronRight className="w-4 h-4" />
          הקודם
        </Button>
        <span className="text-xs text-muted-foreground">
          שקף {slideIndex + 1} מתוך {total}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setSlideIndex(Math.min(total - 1, slideIndex + 1))}
          disabled={slideIndex >= total - 1}
          className="gap-1"
        >
          הבא
          <ChevronLeft className="w-4 h-4" />
        </Button>
      </div>
    </div>
  );
}
