import { useState } from "react";
import { MessageSquarePlus, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { useUser, feedbackTypeLabels, feedbackStatusLabels } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";
import SearchableSelect from "@/components/ui/searchable-select";

const feedbackStatusColors: Record<string, string> = {
  open: "bg-info/10 text-info border-info/20",
  in_progress: "bg-warning/10 text-warning border-warning/20",
  closed: "bg-success/10 text-success border-success/20",
};

const feedbackTypeColors: Record<string, string> = {
  change: "bg-accent/10 text-accent border-accent/20",
  add: "bg-primary/10 text-primary border-primary/20",
  fix: "bg-destructive/10 text-destructive border-destructive/20",
  general: "bg-muted text-muted-foreground border-muted-foreground/20",
};

const FeedbackButton = () => {
  const { currentUser, feedbacks, addFeedback } = useUser();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [type, setType] = useState("general");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const myFeedbacks = feedbacks.filter((f) => f.userId === currentUser.id);

  const handleSubmit = async () => {
    if (!title.trim()) {
      toast({ title: "שגיאה", description: "נא למלא כותרת", variant: "destructive" });
      return;
    }
    setIsSubmitting(true);
    try {
      await addFeedback({ type, title: title.trim(), description: description.trim() });
      toast({ title: "פידבק נשלח בהצלחה ✓", description: "מנהל המערכת יטפל בבקשתך" });
      setDialogOpen(false);
      setTitle("");
      setDescription("");
      setType("general");
    } catch {
      toast({ title: "שגיאה בשליחת פידבק", variant: "destructive" });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <div className="flex gap-2">
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button variant="outline" size="sm" className="gap-1.5">
              <MessageSquarePlus className="w-4 h-4" />
              שלח פידבק
            </Button>
          </DialogTrigger>
          <DialogContent dir="rtl" className="max-w-md">
            <DialogHeader>
              <DialogTitle>שלח פידבק למנהל המערכת</DialogTitle>
              <DialogDescription>שלח בקשה לשינוי, הוספה, תיקון או הערה כללית</DialogDescription>
            </DialogHeader>
            <div className="space-y-4 pt-2">
              <div className="space-y-2">
                <Label>סוג הבקשה</Label>
                <SearchableSelect value={type} onValueChange={setType}
                  options={[
                    { value: "change", label: "🔄 שינוי" },
                    { value: "add", label: "➕ הוספה" },
                    { value: "fix", label: "🔧 תיקון" },
                    { value: "general", label: "💬 כללי" },
                  ]}
                />
              </div>
              <div className="space-y-2">
                <Label>כותרת</Label>
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="תאר בקצרה את הבקשה"
                />
              </div>
              <div className="space-y-2">
                <Label>פירוט</Label>
                <Textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="הסבר מפורט על מה שצריך לשנות/להוסיף/לתקן..."
                  rows={4}
                />
              </div>
              <Button onClick={handleSubmit} disabled={isSubmitting} className="w-full gap-2">
                <Send className="w-4 h-4" />
                {isSubmitting ? "שולח..." : "שלח פידבק"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>

        {myFeedbacks.length > 0 && (
          <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
            <DialogTrigger asChild>
              <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground">
                הפידבקים שלי ({myFeedbacks.length})
              </Button>
            </DialogTrigger>
            <DialogContent dir="rtl" className="max-w-lg max-h-[70vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>הפידבקים שלי</DialogTitle>
                <DialogDescription>היסטוריית הבקשות שלך ותגובות מנהל</DialogDescription>
              </DialogHeader>
              <div className="space-y-3 pt-2">
                {myFeedbacks.map((fb) => (
                  <div key={fb.id} className="p-4 rounded-xl bg-muted/50 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className={feedbackTypeColors[fb.type]}>
                          {feedbackTypeLabels[fb.type]}
                        </Badge>
                        <Badge variant="outline" className={feedbackStatusColors[fb.status]}>
                          {feedbackStatusLabels[fb.status]}
                        </Badge>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {new Date(fb.createdAt).toLocaleDateString("he-IL")}
                      </span>
                    </div>
                    <h4 className="font-medium text-sm">{fb.title}</h4>
                    {fb.description && <p className="text-xs text-muted-foreground">{fb.description}</p>}
                    {fb.adminNotes && (
                      <div className="p-2 rounded-lg bg-primary/5 border border-primary/10">
                        <p className="text-xs text-primary font-medium">תגובת מנהל:</p>
                        <p className="text-xs text-muted-foreground mt-0.5">{fb.adminNotes}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </DialogContent>
          </Dialog>
        )}
      </div>
    </>
  );
};

export default FeedbackButton;
