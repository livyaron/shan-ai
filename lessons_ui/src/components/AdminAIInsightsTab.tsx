import { useState, useEffect } from "react";
import { Brain, CheckCircle2, X, Pencil, Eye, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { supabase } from "@/integrations/supabase/client";
import { useUser } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";

interface CandidateInsight {
  id: number;
  insight_text: string;
  context_type: string;
  context_value: string | null;
  frequency: number;
  confidence: number;
  status: string;
  admin_notes: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
  source_feedback_ids: number[];
}

interface UserPreference {
  id: number;
  user_id: string;
  preference_key: string;
  preference_value: string;
  updated_at: string;
}

const statusLabels: Record<string, string> = {
  pending: "ממתין",
  approved: "מאושר",
  rejected: "נדחה",
  edited: "נערך",
};

const statusColors: Record<string, string> = {
  pending: "bg-warning/10 text-warning border-warning/20",
  approved: "bg-success/10 text-success border-success/20",
  rejected: "bg-destructive/10 text-destructive border-destructive/20",
  edited: "bg-info/10 text-info border-info/20",
};

const contextTypeLabels: Record<string, string> = {
  general: "כללי",
  project_type: "סוג פרויקט",
  domain: "תחום",
  stage: "שלב",
};

const AdminAIInsightsTab = () => {
  const { currentUser } = useUser();
  const [candidates, setCandidates] = useState<CandidateInsight[]>([]);
  const [preferences, setPreferences] = useState<UserPreference[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [editingInsight, setEditingInsight] = useState<CandidateInsight | null>(null);
  const [editText, setEditText] = useState("");
  const [adminNotes, setAdminNotes] = useState("");

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [candidatesRes, prefsRes] = await Promise.all([
        supabase.from("ai_candidate_insights" as any).select("*").order("created_at", { ascending: false }),
        supabase.from("ai_user_preferences" as any).select("*").order("updated_at", { ascending: false }),
      ]);

      if (candidatesRes.data) setCandidates(candidatesRes.data as any[]);
      if (prefsRes.data) setPreferences(prefsRes.data as any[]);
    } catch (e) {
      console.error("Failed to load AI insights data:", e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleApprove = async (insight: CandidateInsight) => {
    try {
      // Update candidate status
      await supabase
        .from("ai_candidate_insights" as any)
        .update({
          status: "approved",
          reviewed_by: currentUser.id,
          reviewed_at: new Date().toISOString(),
          admin_notes: adminNotes || null,
        })
        .eq("id", insight.id);

      // Create global insight
      await supabase
        .from("ai_global_insights" as any)
        .insert({
          candidate_id: insight.id,
          insight_text: insight.insight_text,
          context_type: insight.context_type,
          context_value: insight.context_value,
          approved_by: currentUser.id,
        });

      toast({ title: "תובנה אושרה ✓", description: "התובנה תשפיע על ניתוחי AI עתידיים" });
      setAdminNotes("");
      loadData();
    } catch {
      toast({ title: "שגיאה באישור תובנה", variant: "destructive" });
    }
  };

  const handleReject = async (insightId: number) => {
    try {
      await supabase
        .from("ai_candidate_insights" as any)
        .update({
          status: "rejected",
          reviewed_by: currentUser.id,
          reviewed_at: new Date().toISOString(),
          admin_notes: adminNotes || null,
        })
        .eq("id", insightId);

      toast({ title: "תובנה נדחתה" });
      setAdminNotes("");
      loadData();
    } catch {
      toast({ title: "שגיאה בדחיית תובנה", variant: "destructive" });
    }
  };

  const handleEditSave = async () => {
    if (!editingInsight || !editText.trim()) return;

    try {
      await supabase
        .from("ai_candidate_insights" as any)
        .update({
          insight_text: editText.trim(),
          status: "edited",
          reviewed_by: currentUser.id,
          reviewed_at: new Date().toISOString(),
          admin_notes: adminNotes || null,
        })
        .eq("id", editingInsight.id);

      // Also create global insight with edited text
      await supabase
        .from("ai_global_insights" as any)
        .insert({
          candidate_id: editingInsight.id,
          insight_text: editText.trim(),
          context_type: editingInsight.context_type,
          context_value: editingInsight.context_value,
          approved_by: currentUser.id,
        });

      toast({ title: "תובנה נערכה ואושרה ✓" });
      setEditingInsight(null);
      setEditText("");
      setAdminNotes("");
      loadData();
    } catch {
      toast({ title: "שגיאה בעריכת תובנה", variant: "destructive" });
    }
  };

  const pendingCandidates = candidates.filter((c) => c.status === "pending");
  const reviewedCandidates = candidates.filter((c) => c.status !== "pending");

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Tabs defaultValue="pending" dir="rtl">
        <TabsList>
          <TabsTrigger value="pending" className="gap-1.5">
            ממתינים לאישור
            {pendingCandidates.length > 0 && (
              <Badge variant="destructive" className="text-[10px] px-1.5 py-0">
                {pendingCandidates.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="reviewed">נבדקו</TabsTrigger>
          <TabsTrigger value="preferences">העדפות משתמשים</TabsTrigger>
        </TabsList>

        <TabsContent value="pending" className="mt-4 space-y-3">
          {pendingCandidates.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <Brain className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">אין תובנות ממתינות לאישור</p>
            </div>
          ) : (
            pendingCandidates.map((insight) => (
              <Card key={insight.id} className="border-0 shadow-sm">
                <CardContent className="p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm leading-relaxed flex-1">{insight.insight_text}</p>
                    <Badge variant="outline" className={statusColors[insight.status]}>
                      {statusLabels[insight.status]}
                    </Badge>
                  </div>

                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span>הקשר: {contextTypeLabels[insight.context_type] || insight.context_type}</span>
                    {insight.context_value && <span>· {insight.context_value}</span>}
                    <span>· תדירות: {insight.frequency}</span>
                    <span>· ביטחון: {insight.confidence}%</span>
                  </div>

                  <div>
                    <Label className="text-xs">הערות מנהל (אופציונלי)</Label>
                    <Input
                      value={adminNotes}
                      onChange={(e) => setAdminNotes(e.target.value)}
                      placeholder="הוסף הערה..."
                      className="mt-1 text-sm"
                    />
                  </div>

                  <div className="flex gap-2 justify-end">
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1 text-destructive"
                      onClick={() => handleReject(insight.id)}
                    >
                      <X className="w-3.5 h-3.5" />
                      דחה
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1"
                      onClick={() => {
                        setEditingInsight(insight);
                        setEditText(insight.insight_text);
                      }}
                    >
                      <Pencil className="w-3.5 h-3.5" />
                      ערוך
                    </Button>
                    <Button
                      size="sm"
                      className="gap-1 bg-success text-success-foreground hover:bg-success/90"
                      onClick={() => handleApprove(insight)}
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      אשר
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </TabsContent>

        <TabsContent value="reviewed" className="mt-4 space-y-3">
          {reviewedCandidates.length === 0 ? (
            <p className="text-center py-12 text-sm text-muted-foreground">אין תובנות שנבדקו</p>
          ) : (
            reviewedCandidates.map((insight) => (
              <Card key={insight.id} className="border-0 shadow-sm">
                <CardContent className="p-4 space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm leading-relaxed flex-1">{insight.insight_text}</p>
                    <Badge variant="outline" className={statusColors[insight.status]}>
                      {statusLabels[insight.status]}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span>הקשר: {contextTypeLabels[insight.context_type] || insight.context_type}</span>
                    <span>· תדירות: {insight.frequency}</span>
                    {insight.admin_notes && <span>· הערות: {insight.admin_notes}</span>}
                    {insight.reviewed_at && (
                      <span>· נבדק: {new Date(insight.reviewed_at).toLocaleDateString("he-IL")}</span>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </TabsContent>

        <TabsContent value="preferences" className="mt-4">
          {preferences.length === 0 ? (
            <p className="text-center py-12 text-sm text-muted-foreground">אין העדפות משתמשים שמורות</p>
          ) : (
            <Card className="border-0 shadow-sm">
              <CardContent className="p-4">
                <div className="space-y-2">
                  {preferences.map((pref) => (
                    <div key={pref.id} className="flex items-center justify-between p-3 rounded-lg bg-muted/50 text-sm">
                      <div>
                        <span className="font-medium">{pref.user_id}</span>
                        <span className="text-muted-foreground mx-2">·</span>
                        <span className="text-muted-foreground">{pref.preference_key}: {pref.preference_value}</span>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {new Date(pref.updated_at).toLocaleDateString("he-IL")}
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>

      {/* Edit dialog */}
      <Dialog open={!!editingInsight} onOpenChange={(open) => !open && setEditingInsight(null)}>
        <DialogContent dir="rtl" className="max-w-md">
          <DialogHeader>
            <DialogTitle>עריכת תובנה</DialogTitle>
            <DialogDescription>ערוך את התובנה לפני אישורה כגלובלית</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>טקסט התובנה</Label>
              <Textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                className="mt-1.5 min-h-[100px]"
                dir="rtl"
              />
            </div>
            <div>
              <Label>הערות מנהל</Label>
              <Input
                value={adminNotes}
                onChange={(e) => setAdminNotes(e.target.value)}
                className="mt-1.5"
                placeholder="הערה אופציונלית..."
              />
            </div>
            <Button onClick={handleEditSave} className="w-full">
              שמור ואשר
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default AdminAIInsightsTab;
