import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, ClipboardList, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import SearchableSelect from "@/components/ui/searchable-select";
import SearchableMultiSelect from "@/components/ui/searchable-multi-select";
import SiteCombobox from "@/components/SiteCombobox";
import { stationTypeLabels, useUser } from "@/context/UserContext";
import type { Project, StationType } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";
import { isLinked, missingLabel, projectProblems } from "@/lib/projectProblems";

/**
 * השלמת נתוני פרויקטים (PLAN-lessons-projects.md L4): what the master file
 * cannot know — station type, site, equipment — for every project, in one
 * table. "Missing" is lib/projectProblems (the same rule the PM sees on their
 * cards). Station type can be set for many rows at once. Admin only.
 */

const STATION_OPTIONS = (Object.keys(stationTypeLabels) as StationType[]).map((k) => ({ value: k, label: stationTypeLabels[k] }));

interface Draft {
  stationType?: StationType;
  site?: string;
  equipmentIds?: number[];
}

const ProjectDataPanel = () => {
  const { projects, equipment, users, updateProject, updateProjectEquipment } = useUser();
  const [onlyMissing, setOnlyMissing] = useState(true);
  const [query, setQuery] = useState("");
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [selected, setSelected] = useState<number[]>([]);
  const [bulkStation, setBulkStation] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const managerName = useMemo(() => new Map(users.map((u) => [u.id, u.name.trim()])), [users]);
  const missingCount = projects.filter((p) => projectProblems(p).length > 0).length;
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return projects
      .filter((p) => !onlyMissing || projectProblems(p).length > 0)
      .filter((p) => !q || p.name.toLowerCase().includes(q) || (managerName.get(p.managerId) ?? "").toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name, "he"));
  }, [projects, onlyMissing, query, managerName]);

  const setDraft = (id: number, d: Draft) => setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...d } }));

  const saveRow = async (p: Project) => {
    const d = drafts[p.id];
    if (!d) return;
    setBusy(`r${p.id}`);
    try {
      if (d.stationType !== undefined || d.site !== undefined) {
        await updateProject(p.id, {
          ...(d.stationType !== undefined ? { stationType: d.stationType } : {}),
          ...(d.site !== undefined ? { site: d.site } : {}),
        });
      }
      if (d.equipmentIds !== undefined) await updateProjectEquipment(p.id, d.equipmentIds);
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
      toast({ title: `${p.name} עודכן ✓` });
    } catch (e) {
      toast({ title: "השמירה נכשלה", description: e instanceof Error ? e.message : String(e), variant: "destructive" });
    } finally {
      setBusy(null);
    }
  };

  const applyBulk = async () => {
    if (!bulkStation || selected.length === 0) return;
    setBusy("bulk");
    let failed = 0;
    for (const id of selected) {
      try {
        await updateProject(id, { stationType: bulkStation as StationType });
      } catch {
        failed += 1;
      }
    }
    setBusy(null);
    setSelected([]);
    toast(failed
      ? { title: `${selected.length - failed} עודכנו, ${failed} נכשלו`, variant: "destructive" }
      : { title: `סוג התחנה עודכן ב-${selected.length} פרויקטים ✓` });
  };

  return (
    <Card className="border-0 shadow-sm">
      <CardHeader className="space-y-3">
        <CardTitle className="text-base font-heading flex flex-wrap items-center gap-2">
          <ClipboardList className="w-4 h-4" />
          השלמת נתוני פרויקטים
          {missingCount > 0 ? (
            <Badge variant="outline" className="bg-warning/10 text-warning border-warning/30 gap-1">
              <AlertTriangle className="w-3 h-3" />
              {missingCount} חסרי נתונים
            </Badge>
          ) : (
            <Badge variant="outline" className="bg-success/10 text-success border-success/30 gap-1">
              <CheckCircle className="w-3 h-3" />
              כל הפרויקטים מלאים
            </Badge>
          )}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          סוג תחנה, אתר וציוד — מה שקובץ המאסטר לא יודע. שם, שלב, סוג פרויקט, סיכון ומנה״פ של פרויקט מקושר (🔗) מגיעים מהקובץ.
          מנה״פ רואה את אותו סימון "חסר" על הפרויקטים שלו.
        </p>
        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="חיפוש לפי שם או מנה״פ" className="sm:max-w-xs" />
          <div className="flex items-center gap-2">
            <Switch id="only-missing" checked={onlyMissing} onCheckedChange={setOnlyMissing} />
            <Label htmlFor="only-missing" className="text-sm">רק חסרי נתונים</Label>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/50 p-2">
          <span className="text-xs">סומנו {selected.length}:</span>
          <SearchableSelect value={bulkStation} onValueChange={setBulkStation} options={STATION_OPTIONS}
            placeholder="סוג תחנה לכולם…" className="w-48" />
          <Button size="sm" disabled={!bulkStation || selected.length === 0 || busy !== null} onClick={applyBulk}>
            {busy === "bulk" ? <Loader2 className="w-4 h-4 animate-spin" /> : "החל על המסומנים"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(selected.length === rows.length ? [] : rows.map((r) => r.id))}>
            {selected.length === rows.length && rows.length > 0 ? "נקה סימון" : `סמן הכל (${rows.length})`}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {rows.length === 0 && <p className="text-sm text-muted-foreground text-center py-6">אין פרויקטים להצגה</p>}
        {rows.map((p) => {
          const d = drafts[p.id] ?? {};
          const problems = projectProblems({
            stationTypeSet: d.stationType !== undefined || p.stationTypeSet,
            site: d.site ?? p.site,
            equipmentIds: d.equipmentIds ?? p.equipmentIds,
          });
          return (
            <div key={p.id} className={`space-y-2 p-3 rounded-xl ${problems.length ? "bg-warning/5 ring-1 ring-warning/30" : "bg-muted/50"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Checkbox checked={selected.includes(p.id)}
                  onCheckedChange={(c) => setSelected((prev) => (c ? [...prev, p.id] : prev.filter((x) => x !== p.id)))} />
                <span className="text-sm font-medium">{p.name}</span>
                {isLinked(p) && <span className="text-xs" title={p.shanIdentifier}>🔗</span>}
                <span className="text-xs text-muted-foreground">{managerName.get(p.managerId) ?? ""}</span>
                {problems.length > 0 && <Badge variant="outline" className="text-warning border-warning/40 text-[11px]">{missingLabel(problems)}</Badge>}
              </div>
              <div className="grid gap-2 md:grid-cols-[12rem_1fr_1fr_auto] items-center">
                <SearchableSelect
                  value={d.stationType ?? (p.stationTypeSet ? p.stationType : "")}
                  onValueChange={(v) => setDraft(p.id, { stationType: v as StationType })}
                  options={STATION_OPTIONS} placeholder="סוג תחנה…" />
                <SiteCombobox value={d.site ?? p.site ?? ""} onValueChange={(v) => setDraft(p.id, { site: v })} />
                <SearchableMultiSelect
                  value={(d.equipmentIds ?? p.equipmentIds).map(String)}
                  onChange={(v) => setDraft(p.id, { equipmentIds: v.map(Number) })}
                  options={equipment.map((e) => ({ value: String(e.id), label: e.name }))}
                  placeholder="ציוד / נושאים…" />
                <Button size="sm" disabled={!drafts[p.id] || busy !== null} onClick={() => saveRow(p)}>
                  {busy === `r${p.id}` ? <Loader2 className="w-4 h-4 animate-spin" /> : "שמור"}
                </Button>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
};

export default ProjectDataPanel;
