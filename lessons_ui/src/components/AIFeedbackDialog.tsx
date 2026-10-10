import { useState } from "react";
import { MessageSquare, Loader2, CheckCircle2, AlertTriangle, Globe, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import SearchableSelect from "@/components/ui/searchable-select";
import { Label } from "@/components/ui/label";
import { supabase } from "@/integrations/supabase/client";
import { useUser } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";

interface AIFeedbackDialogProps {
  contextType: "dashboard" | "project";
  contextId?: string | number;
}

const classificationLabels: Record<string, string> = {
  public: "פומבי",
  personal: "אישי",
  // Backward compatibility
  professional: "פומבי",
  both: "פומבי",
};

const classificationColors: Record<string, string> = {
  public: "bg-primary/10 text-primary border-primary/20",
  personal: "bg-accent/10 text-accent border-accent/20",
  professional: "bg-primary/10 text-primary border-primary/20",
  both: "bg-primary/10 text-primary border-primary/20",
};

const classificationDescriptions: Record<string, string> = {
  public: "תובנות כלליות שישפיעו על כל המשתמשים. דורש אישור מנהל לקחים.",
  personal: "העדפות אישיות שהוחלו מיידית על הניתוחים שלך.",
  professional: "תובנות כלליות שישפיעו על כל המשתמשים. דורש אישור מנהל לקחים.",
  both: "תובנות כלליות שישפיעו על כל המשתמשים. דורש אישור מנהל לקחים.",
};

const preferenceKeyLabels: Record<string, string> = {
  style: "סגנון כתיבה",
  length: "אורך",
  focus: "מיקוד",
  format: "פורמט",
  detail_level: "רמת פירוט",
};

interface FeedbackResult {
  id: number;
  classification: string;
  extracted_public: { text: string }[];
  extracted_personal: { key: string; value: string }[];
}

const AIFeedbackDialog = ({ contextType, contextId }: AIFeedbackDialogProps) => {
  const { currentUser } = useUser();
  const [open, setOpen] = useState(false);
  const [feedbackText, setFeedbackText] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [result, setResult] = useState<FeedbackResult | null>(null);
  const [overrideClassification, setOverrideClassification] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!feedbackText.trim()) {
      toast({ title: "נא להזין פידבק", variant: "destructive" });
      return;
    }

    setIsSubmitting(true);
    setResult(null);

    try {
      const { data, error } = await supabase.functions.invoke("classify-ai-feedback", {
        body: {
          feedback_text: feedbackText.trim(),
          context_type: contextType,
          context_id: contextId ? String(contextId) : null,
          user_id: currentUser.id,
          user_name: currentUser.name,
        },
      });

      if (error) throw error;

      setResult(data);
      toast({ title: "הפידבק נשמר ✓", description: "תודה! הפידבק שלך ישפר את ניתוחי ה-AI בעתיד." });
    } catch (e: any) {
      console.error("Feedback submit error:", e, "message:", e?.message, "context:", e?.context);
      toast({ title: "שגיאה בשליחת פידבק", description: e?.message || String(e), variant: "destructive" });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOverride = async () => {
    if (!overrideClassification || !result) return;

    try {
      await supabase
        .from("ai_feedback" as any)
        .update({ user_override_classification: overrideClassification })
        .eq("id", result.id);

      toast({ title: "הסיווג עודכן ✓" });
    } catch {
      toast({ title: "שגיאה בעדכון סיווג", variant: "destructive" });
    }
  };

  const handleClose = (isOpen: boolean) => {
    setOpen(isOpen);
    if (!isOpen) {
      setFeedbackText("");
      setResult(null);
      setOverrideClassification(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground hover:text-foreground">
          <MessageSquare className="w-3.5 h-3.5" />
          פידבק ללמידת AI
        </Button>
      </DialogTrigger>
      <DialogContent dir="rtl" className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <MessageSquare className="w-5 h-5 text-accent" />
            פידבק על ניתוח AI
          </DialogTitle>
          <DialogDescription>
            הפידבק שלך עוזר לשפר את איכות הניתוח. ציין טעויות, חוסרים, או העדפות סגנון.
          </DialogDescription>
        </DialogHeader>

        {!result ? (
          <div className="space-y-4 pt-2">
            <div>
              <Label>הפידבק שלך</Label>
              <Textarea
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                placeholder="למשל: הניתוח לא התייחס לסיכון בעבודות האזרחיות, אני מעדיף תובנות קצרות יותר..."
                className="mt-1.5 min-h-[120px]"
                dir="rtl"
              />
            </div>
            <Button
              onClick={handleSubmit}
              disabled={isSubmitting || !feedbackText.trim()}
              className="w-full"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  מנתח ושומר...
                </>
              ) : (
                "שלח פידבק"
              )}
            </Button>
          </div>
        ) : (
          <div className="space-y-4 pt-2">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-success" />
              <span className="text-sm font-medium">הפידבק נשמר וסווג בהצלחה</span>
            </div>

            <div className="p-3 rounded-xl bg-muted/50 space-y-3">
              {/* Classification badge with description */}
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-muted-foreground">סיווג כללי:</span>
                  <Badge variant="outline" className={`gap-1 ${classificationColors[result.classification] || classificationColors.public}`}>
                    {result.classification === "personal" ? (
                      <User className="w-3 h-3" />
                    ) : (
                      <Globe className="w-3 h-3" />
                    )}
                    {classificationLabels[result.classification] || "פומבי"}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {classificationDescriptions[result.classification] || classificationDescriptions.public}
                </p>
              </div>

              {/* Public items */}
              {result.extracted_public.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1">
                    <Globe className="w-3 h-3" />
                    שינויים פומביים (ממתינים לאישור מנהל):
                  </p>
                  <ul className="space-y-1">
                    {result.extracted_public.map((m, i) => (
                      <li key={i} className="text-xs flex items-start gap-1.5">
                        <AlertTriangle className="w-3 h-3 text-warning mt-0.5 shrink-0" />
                        {m.text}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Personal items */}
              {result.extracted_personal.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1">
                    <User className="w-3 h-3" />
                    העדפות אישיות (הוחלו מיידית):
                  </p>
                  <ul className="space-y-1">
                    {result.extracted_personal.map((p, i) => (
                      <li key={i} className="text-xs text-muted-foreground">
                        {preferenceKeyLabels[p.key] || p.key}: {p.value}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* Override classification */}
            <div className="space-y-2">
              <Label className="text-xs">לא מסכים עם הסיווג? שנה אותו:</Label>
              <div className="flex items-center gap-2">
                <SearchableSelect
                  value={overrideClassification || ""}
                  onValueChange={setOverrideClassification}
                  className="flex-1"
                  placeholder="בחר סיווג חלופי"
                  options={[
                    { value: "public", label: "פומבי" },
                    { value: "personal", label: "אישי" },
                  ]}
                />
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleOverride}
                  disabled={!overrideClassification}
                >
                  עדכן
                </Button>
              </div>
            </div>

            <Button variant="outline" onClick={() => handleClose(false)} className="w-full">
              סגור
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default AIFeedbackDialog;
