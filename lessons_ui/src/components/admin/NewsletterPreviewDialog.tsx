import { useRef, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Download, Loader2 } from "lucide-react";
import NewsletterPreview from "./NewsletterPreview";
import { exportNewsletterToPDF } from "@/lib/newsletterExport";
import type { NewsletterStats } from "@/lib/newsletterStats";
import { toast } from "@/hooks/use-toast";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  stats: NewsletterStats | null;
}

const NewsletterPreviewDialog = ({ open, onOpenChange, stats }: Props) => {
  const previewRef = useRef<HTMLDivElement>(null);
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    if (!stats) return;
    setDownloading(true);
    try {
      await exportNewsletterToPDF(stats, `עלון-פעילות-${stats.range.labelHe}.pdf`);
      toast({ title: "PDF הורד בהצלחה ✓" });
    } catch (err) {
      console.error(err);
      toast({ title: "שגיאה ביצירת PDF", variant: "destructive" });
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[900px] max-h-[90vh] overflow-y-auto" dir="rtl">
        <DialogHeader>
          <DialogTitle>תצוגה מקדימה — עלון פעילות</DialogTitle>
        </DialogHeader>
        <div className="flex justify-center bg-muted/30 rounded-lg p-4">
          {stats && <NewsletterPreview ref={previewRef} stats={stats} />}
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>סגור</Button>
          <Button
            onClick={handleDownload}
            disabled={downloading || !stats}
            className="bg-accent text-accent-foreground hover:bg-accent/90 gap-1.5"
          >
            {downloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            הורד PDF
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default NewsletterPreviewDialog;
