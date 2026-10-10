import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sparkles, RefreshCw, Loader2, TrendingUp, AlertTriangle, Layers, Flame } from "lucide-react";
import { useUser } from "@/context/UserContext";
import { findInsights, type AnalyticsInsight, type InsightKind } from "@/lib/adminAnalytics";
import { buildLessonDrilldownPath } from "@/lib/drilldown";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";

const kindMeta: Record<InsightKind, { label: string; icon: any; color: string }> = {
  risk:      { label: "מוקד סיכון", icon: Flame,         color: "bg-destructive/10 text-destructive border-destructive/20" },
  anomaly:   { label: "חריג",       icon: AlertTriangle, color: "bg-warning/10 text-warning border-warning/20" },
  trend:     { label: "מגמה",       icon: TrendingUp,    color: "bg-info/10 text-info border-info/20" },
  repeating: { label: "חוזר",       icon: Layers,        color: "bg-accent/10 text-accent border-accent/20" },
};

const AdminInsightsPanel = () => {
  const navigate = useNavigate();
  const { lessons, projects, implementations, referentReviews, users } = useUser();
  const [phrasings, setPhrasings] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);

  const baseInsights: AnalyticsInsight[] = useMemo(
    () => findInsights(lessons, implementations, referentReviews, projects, users),
    [lessons, implementations, referentReviews, projects, users],
  );

  const generate = async () => {
    if (baseInsights.length === 0) {
      toast({ title: "אין דפוסים שעוברים את ספי החומרה" });
      return;
    }
    setLoading(true);
    try {
      const { data, error } = await supabase.functions.invoke("admin-insights", {
        body: {
          patterns: baseInsights.map((i) => ({
            id: i.id, kind: i.kind, title: i.title, evidence: i.evidence,
          })),
        },
      });
      if (error) throw error;
      const map: Record<string, string> = {};
      for (const p of (data?.phrasings ?? []) as { id: string; sentence: string }[]) {
        if (p?.id && p?.sentence) map[p.id] = p.sentence;
      }
      setPhrasings(map);
    } catch (e: any) {
      console.error(e);
      toast({ title: "שגיאה ביצירת תובנות AI", description: String(e?.message ?? e), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  // Auto-generate phrasings on first mount when there are insights.
  useEffect(() => {
    if (baseInsights.length > 0 && Object.keys(phrasings).length === 0) {
      generate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseInsights.length]);

  return (
    <Card className="border-0 shadow-sm overflow-hidden">
      <CardHeader className="bg-gradient-to-l from-accent/5 to-primary/5">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Sparkles className="w-5 h-5 text-accent" />
            תובנות AI ניהוליות
          </CardTitle>
          <Button variant="outline" size="sm" onClick={generate} disabled={loading} className="gap-1.5">
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {loading ? "מנתח..." : "רענן תובנות"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-5">
        {baseInsights.length === 0 ? (
          <div className="text-center py-10 text-muted-foreground text-sm">
            לא זוהו דפוסים שעוברים את ספי החומרה ההתחלתיים
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {baseInsights.map((ins) => {
              const meta = kindMeta[ins.kind];
              const Icon = meta.icon;
              const sentence = phrasings[ins.id] || ins.title;
              return (
                <button
                  key={ins.id}
                  type="button"
                  onClick={() => navigate(buildLessonDrilldownPath(ins.lessonIds, "repository"))}
                  className="text-right p-4 rounded-xl border bg-card hover:bg-muted/40 transition-colors"
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <Badge variant="outline" className={`text-[10px] ${meta.color}`}>
                      <Icon className="w-3 h-3 ml-1" />
                      {meta.label}
                    </Badge>
                    <span className="text-[11px] text-muted-foreground">{ins.lessonIds.length} לקחים</span>
                  </div>
                  <p className="text-sm leading-relaxed text-foreground">{sentence}</p>
                  <p className="text-[11px] text-muted-foreground mt-1.5">{ins.evidence}</p>
                </button>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default AdminInsightsPanel;
