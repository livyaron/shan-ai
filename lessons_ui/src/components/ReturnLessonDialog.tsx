import { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { RETURN_REASONS } from "@/lib/constants";
import { RotateCcw } from "lucide-react";
import SearchableSelect from "@/components/ui/searchable-select";

interface ReturnLessonDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (reason: string, customReason?: string) => Promise<void>;
  /** Title of the lesson being returned */
  lessonTitle: string;
}

/** Dialog for returning a lesson to its creator with a structured reason */
const ReturnLessonDialog = ({ open, onOpenChange, onConfirm, lessonTitle }: ReturnLessonDialogProps) => {
  const [reason, setReason] = useState("");
  const [customReason, setCustomReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleConfirm = async () => {
    if (!reason) return;
    setIsSubmitting(true);
    try {
      await onConfirm(reason, reason === "אחר" ? customReason : undefined);
      // Reset state
      setReason("");
      setCustomReason("");
      onOpenChange(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const isValid = reason && (reason !== "אחר" || customReason.trim());

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent dir="rtl" className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <RotateCcw className="w-5 h-5 text-warning" />
            החזרת לקח להשלמה
          </DialogTitle>
          <DialogDescription>
            הלקח "{lessonTitle}" יוחזר ליוצר להשלמה. נא לבחור סיבה.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-2">
          <div className="space-y-2">
            <Label>סיבת ההחזרה</Label>
            <SearchableSelect value={reason} onValueChange={setReason} placeholder="בחר סיבה..."
              options={[
                ...RETURN_REASONS.map((r) => ({ value: String(r), label: r })),
              ]}
            />
          </div>

          {reason === "אחר" && (
            <div className="space-y-2">
              <Label>פרט את הסיבה</Label>
              <Textarea
                value={customReason}
                onChange={(e) => setCustomReason(e.target.value)}
                placeholder="תאר את הסיבה להחזרה..."
                rows={3}
              />
            </div>
          )}
        </div>

        <DialogFooter className="flex-row-reverse gap-2 pt-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            ביטול
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={!isValid || isSubmitting}
            className="bg-warning text-warning-foreground hover:bg-warning/90 gap-1.5"
          >
            <RotateCcw className="w-4 h-4" />
            {isSubmitting ? "מחזיר..." : "החזר ליוצר"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ReturnLessonDialog;
