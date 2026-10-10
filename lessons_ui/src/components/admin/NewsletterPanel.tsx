import { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Newspaper, Eye, Info } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import SearchableSelect from "@/components/ui/searchable-select";
import { roleLabels, useUser } from "@/context/UserContext";
import {
  type PeriodKind, defaultPeriodKey, listPeriods, periodRange,
  formatDateRange, periodKindLabel,
} from "@/lib/newsletterPeriods";
import { lessonsCreatedInRange, bucketBreakdown } from "@/lib/newsletterQuery";
import { computeNewsletterStats, type NewsletterStats } from "@/lib/newsletterStats";
import NewsletterPreviewDialog from "./NewsletterPreviewDialog";

const NewsletterPanel = () => {
  const { lessons, projects, users, implementations, referentReviews } = useUser();
  const [kind, setKind] = useState<PeriodKind>("monthly");
  const [periodKey, setPeriodKey] = useState<string>(() => defaultPeriodKey("monthly"));
  const [dialogOpen, setDialogOpen] = useState(false);
  const [stats, setStats] = useState<NewsletterStats | null>(null);

  const periodOptions = useMemo(() => listPeriods(kind), [kind]);
  const currentRange = useMemo(() => periodRange(kind, periodKey), [kind, periodKey]);

  const previewBreakdown = useMemo(
    () => bucketBreakdown(lessonsCreatedInRange(lessons, currentRange)),
    [lessons, currentRange],
  );


  const handleKindChange = (newKind: string) => {
    const k = newKind as PeriodKind;
    setKind(k);
    setPeriodKey(defaultPeriodKey(k));
  };

  const handleGenerate = () => {
    const computed = computeNewsletterStats({
      range: currentRange,
      lessons, projects, users, implementations, reviews: referentReviews, roleLabels,
    });
    setStats(computed);
    setDialogOpen(true);
  };

  return (
    <>
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading flex items-center gap-2">
            <Newspaper className="w-4 h-4 text-accent" />
            הפקת עלון פעילות תקופתי
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            הפקת עלון מסכם של פעילות המשתמשים במערכת לתקופה נבחרת — להפצה פנים-ארגונית.
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label className="text-xs">סוג תקופה</Label>
              <SearchableSelect
                value={kind}
                onValueChange={handleKindChange}
                placeholder="בחר סוג תקופה"
                options={(Object.keys(periodKindLabel) as PeriodKind[]).map((k) => ({
                  value: k, label: periodKindLabel[k],
                }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">תקופה</Label>
              <SearchableSelect
                value={periodKey}
                onValueChange={setPeriodKey}
                placeholder="בחר תקופה"
                options={periodOptions.map((p) => ({ value: p.key, label: p.label }))}
              />
            </div>
          </div>

          <div className="text-xs text-muted-foreground rounded-lg bg-muted/40 p-3 space-y-2">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1">
              <span>
                תקופה: <span className="font-medium text-foreground">{formatDateRange(currentRange.start, currentRange.end)}</span>
              </span>
              <span className="flex items-center gap-1">
                {previewBreakdown.total} לקחים נוצרו בתקופה
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Info className="w-3.5 h-3.5 cursor-help" />
                    </TooltipTrigger>
                    <TooltipContent className="max-w-[240px] text-xs">
                      מקור אמת אחיד: נספרים כל הלקחים לפי תאריך יצירה בטווח הנבחר. "אושרו והופצו" = לקחים שאושרו בפועל. אותה לוגיקה חלה על הדשבורד, המסך והעלון.
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </span>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>✔ {previewBreakdown.approved} מאושרים</span>
              <span>⏳ {previewBreakdown.pending} ממתינים</span>
              <span>📝 {previewBreakdown.draft} טיוטות</span>
            </div>
          </div>

          <Button onClick={handleGenerate} className="bg-accent text-accent-foreground hover:bg-accent/90 gap-1.5">
            <Eye className="w-4 h-4" />
            צור תצוגה מקדימה
          </Button>
        </CardContent>
      </Card>

      <NewsletterPreviewDialog open={dialogOpen} onOpenChange={setDialogOpen} stats={stats} />
    </>
  );
};

export default NewsletterPanel;
