import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, FolderPlus, GitMerge, Link2, Link2Off, Loader2, MapPin } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import SearchableSelect from "@/components/ui/searchable-select";
import { SUPABASE_URL } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";
import {
  filterLinkRows,
  freeMasters,
  MANAGER_PROBLEM_LABELS,
  masterLabel,
  rowState,
  type LinkRow,
  type LinksPayload,
} from "@/lib/projectLinks";

/**
 * קישור פרויקטים (PLAN-lessons-projects.md L1): each lessons project ↔ the
 * project in the weekly master file. Suggestions are name similarity only — a
 * hint; nothing is linked until the admin clicks. Duplicates are merged (the
 * lessons move, the duplicate is deleted). Once every lessons project is linked
 * or kept local, the file's remaining projects are created in one click.
 * Server: /lessons/api/_project_links (Shan-AI admin only).
 */

const ProjectLinksPanel = () => {
  const [data, setData] = useState<LinksPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [merging, setMerging] = useState<LinkRow | null>(null);
  const [mergeTarget, setMergeTarget] = useState("");
  const [confirmCreate, setConfirmCreate] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${SUPABASE_URL}/_project_links`, { credentials: "same-origin" });
      if (r.status === 403) {
        setError("קישור פרויקטים זמין למנהל מערכת של Shan-AI בלבד.");
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
      setError(null);
    } catch {
      setError("טעינת הפרויקטים נכשלה. נסה לרענן.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (key: string, body: Record<string, unknown>, done: string, reload = false) => {
    setBusy(key);
    try {
      const r = await fetch(`${SUPABASE_URL}/_project_links`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) {
        toast({ title: res.message || "הפעולה נכשלה", variant: "destructive" });
        return false;
      }
      setData(res);
      toast({ title: done });
      // Merge deletes a project and create adds many: the module's own lists must reload.
      if (reload) window.setTimeout(() => window.location.reload(), 800);
      return true;
    } catch {
      toast({ title: "הפעולה נכשלה", variant: "destructive" });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const visible = useMemo(() => (data ? filterLinkRows(data.projects, query, onlyOpen) : []), [data, query, onlyOpen]);

  if (error) {
    return (
      <Card className="border-0 shadow-sm">
        <CardContent className="py-8 text-center text-sm text-muted-foreground">{error}</CardContent>
      </Card>
    );
  }
  if (!data) {
    return (
      <div className="flex justify-center py-10">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const s = data.summary;
  const mergeOptions = merging
    ? data.projects.filter((p) => p.id !== merging.id).map((p) => ({
        value: String(p.id),
        label: `${p.name}${p.identifier ? " · מקושר לקובץ" : ""} (${p.lessons_count} לקחים)`,
      }))
    : [];

  return (
    <div className="space-y-6">
      <Card className="border-0 shadow-sm">
        <CardHeader className="space-y-3">
          <CardTitle className="text-base font-heading flex flex-wrap items-center gap-2">
            <Link2 className="w-4 h-4" />
            קישור פרויקטים לקובץ המאסטר
            {s.undecided > 0 ? (
              <Badge variant="outline" className="bg-warning/10 text-warning border-warning/30 gap-1">
                <AlertTriangle className="w-3 h-3" />
                {s.undecided} ממתינים להחלטה
              </Badge>
            ) : (
              <Badge variant="outline" className="bg-success/10 text-success border-success/30 gap-1">
                <CheckCircle className="w-3 h-3" />
                כל פרויקטי הלקחים טופלו
              </Badge>
            )}
          </CardTitle>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-center">
            {[
              ["פרויקטים בלקחים", s.lessons_total],
              ["מקושרים", s.linked],
              ["מקומיים", s.local],
              ["בקובץ (פעילים)", s.master_total],
              ["ייווצרו מהקובץ", s.to_create],
            ].map(([label, n]) => (
              <div key={label as string} className="rounded-xl bg-muted/50 p-2">
                <div className="text-lg font-bold">{n}</div>
                <div className="text-xs text-muted-foreground">{label}</div>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            ההצעות מבוססות על דמיון שמות בלבד — שום דבר לא מקושר עד שלוחצים. "מזג" מעביר את הלקחים והמלצות היישום
            לפרויקט אחר ומוחק את הכפול. "מקומי" = פרויקט שלא קיים בקובץ.
          </p>
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="חיפוש לפי שם או מנה״פ"
              className="sm:max-w-xs" />
            <div className="flex items-center gap-2">
              <Switch id="only-open" checked={onlyOpen} onCheckedChange={setOnlyOpen} />
              <Label htmlFor="only-open" className="text-sm">רק ממתינים להחלטה</Label>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {visible.length === 0 && <p className="text-sm text-muted-foreground text-center py-6">אין פרויקטים להצגה</p>}
          {visible.map((r) => {
            const state = rowState(r);
            const options = freeMasters(data.masters, r.id).map((m) => ({ value: m.identifier, label: masterLabel(m) }));
            return (
              <div key={r.id}
                className={`flex flex-col gap-2 p-3 rounded-xl ${state === "open" ? "bg-warning/5 ring-1 ring-warning/30" : "bg-muted/50"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{r.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {r.lessons_count} לקחים{r.manager_name ? ` · ${r.manager_name.trim()}` : ""}{r.site ? ` · ${r.site}` : ""}
                  </span>
                  {state === "linked" && (
                    <Badge variant="outline" className="bg-success/10 text-success border-success/30 gap-1">
                      <Link2 className="w-3 h-3" />
                      {r.linked ? masterLabel(r.linked) : r.identifier}
                    </Badge>
                  )}
                  {r.linked_missing && (
                    <Badge variant="outline" className="bg-destructive/10 text-destructive border-destructive/30">
                      הפרויקט לא פעיל יותר בקובץ
                    </Badge>
                  )}
                  {state === "local" && (
                    <Badge variant="outline" className="gap-1"><MapPin className="w-3 h-3" />מקומי</Badge>
                  )}
                </div>

                {state === "open" && r.suggestions.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {r.suggestions.map((sg) => (
                      <Button key={sg.identifier} size="sm" variant="outline" className="h-auto py-1 text-xs gap-1"
                        disabled={busy !== null}
                        onClick={() => act(`l${r.id}`, { action: "link", project_id: r.id, identifier: sg.identifier },
                          `${r.name} קושר ✓`)}>
                        <Link2 className="w-3 h-3" />
                        {masterLabel(sg)} <span className="text-muted-foreground">({sg.score}%)</span>
                      </Button>
                    ))}
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  {state === "open" && (
                    <>
                      <SearchableSelect
                        value=""
                        onValueChange={(v) => act(`l${r.id}`, { action: "link", project_id: r.id, identifier: v }, `${r.name} קושר ✓`)}
                        options={options}
                        placeholder="קישור לפרויקט אחר מהקובץ…"
                        searchable
                        disabled={busy !== null}
                        className="w-full sm:w-80"
                      />
                      <Button size="sm" variant="ghost" className="gap-1" disabled={busy !== null}
                        onClick={() => act(`k${r.id}`, { action: "local", project_id: r.id, local: true }, `${r.name} נשאר מקומי`)}>
                        <MapPin className="w-3.5 h-3.5" />
                        השאר מקומי
                      </Button>
                    </>
                  )}
                  {state === "linked" && (
                    <Button size="sm" variant="ghost" className="gap-1" disabled={busy !== null}
                      onClick={() => act(`u${r.id}`, { action: "link", project_id: r.id, identifier: null }, `${r.name} נותק`)}>
                      <Link2Off className="w-3.5 h-3.5" />
                      נתק
                    </Button>
                  )}
                  {state === "local" && (
                    <Button size="sm" variant="ghost" disabled={busy !== null}
                      onClick={() => act(`k${r.id}`, { action: "local", project_id: r.id, local: false }, "ההחלטה בוטלה")}>
                      בטל "מקומי"
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" className="gap-1" disabled={busy !== null}
                    onClick={() => { setMerging(r); setMergeTarget(""); }}>
                    <GitMerge className="w-3.5 h-3.5" />
                    מזג לתוך…
                  </Button>
                  {busy !== null && busy.slice(1) === String(r.id) && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading flex items-center gap-2">
            <FolderPlus className="w-4 h-4" />
            יצירת הפרויקטים החסרים מהקובץ ({s.to_create})
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            כל פרויקט פעיל בקובץ שאין לו פרויקט לקחים מקושר ייווצר עם שם, שלב, סוג, סיכון ומנה״פ מהקובץ. סוג התחנה, האתר
            והציוד יסומנו "חסר" להשלמה.
          </p>
          {s.no_manager > 0 && (
            <p className="text-warning">
              {s.no_manager} פרויקטים ייווצרו עם מנה״פ "טרם שויך" — השם שלהם בקובץ לא מקושר למשתמש.
            </p>
          )}
          {s.managers_unlinked.length > 0 && (
            <div className="rounded-xl bg-warning/5 ring-1 ring-warning/30 p-3 space-y-1">
              <div className="font-medium">מנה״פים שעדיין לא קושרו ({s.managers_unlinked.length})</div>
              {s.managers_unlinked.map((m) => (
                <div key={m.name} className="text-xs">
                  {m.name} — {MANAGER_PROBLEM_LABELS[m.problem]} ({m.projects} פרויקטים)
                </div>
              ))}
              <a href="/dashboard/projects/insights/access" className="text-xs text-primary underline">
                קישור שמות מנה״פ למשתמשים (Shan-AI) ←
              </a>
            </div>
          )}
          {s.unknown_stage.length > 0 && (
            <div className="rounded-xl bg-muted/50 p-3 space-y-1">
              <div className="font-medium">לא ייווצרו — שלב לא מוכר ({s.unknown_stage.length})</div>
              {s.unknown_stage.map((u) => (
                <div key={u.identifier} className="text-xs">{u.name || u.identifier} — "{u.stage || "ריק"}"</div>
              ))}
            </div>
          )}
          {s.type_unknown.length > 0 && (
            <p className="text-xs text-muted-foreground">סוגי פרויקט לא מוכרים (ייווצרו כ"הקמה"): {s.type_unknown.join(", ")}</p>
          )}
          <Button className="gap-1.5" disabled={!data.can_create || busy !== null} onClick={() => setConfirmCreate(true)}>
            <FolderPlus className="w-4 h-4" />
            צור {s.to_create} פרויקטים
          </Button>
          {s.undecided > 0 && (
            <p className="text-xs text-muted-foreground">
              זמין אחרי שכל פרויקטי הלקחים קושרו או סומנו כמקומיים — אחרת פרויקט קיים עלול להיווצר פעמיים.
            </p>
          )}
        </CardContent>
      </Card>

      <Dialog open={merging !== null} onOpenChange={(o) => !o && setMerging(null)}>
        <DialogContent dir="rtl">
          <DialogHeader>
            <DialogTitle>מיזוג "{merging?.name}"</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {merging?.lessons_count ?? 0} הלקחים והמלצות היישום של הפרויקט יעברו לפרויקט שתבחר, והפרויקט הזה יימחק. לא
            ניתן לבטל.
          </p>
          <SearchableSelect value={mergeTarget} onValueChange={setMergeTarget} options={mergeOptions}
            placeholder="לתוך איזה פרויקט?" searchable />
          <DialogFooter>
            <Button variant="destructive" disabled={!mergeTarget || busy !== null}
              onClick={async () => {
                if (!merging) return;
                const ok = await act(`m${merging.id}`, { action: "merge", source_id: merging.id, target_id: Number(mergeTarget) },
                  "המיזוג הושלם ✓", true);
                if (ok) setMerging(null);
              }}>
              מזג ומחק את הכפול
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirmCreate} onOpenChange={setConfirmCreate}>
        <DialogContent dir="rtl">
          <DialogHeader>
            <DialogTitle>ליצור {s.to_create} פרויקטים מקובץ המאסטר?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            הפרויקטים יופיעו ברשימת הפרויקטים של מערכת הלקחים, אצל מנה״פ שלהם ואצל מנהלי המערכת.
          </p>
          <DialogFooter>
            <Button disabled={busy !== null}
              onClick={async () => {
                const ok = await act("create", { action: "create_missing" }, "הפרויקטים נוצרו ✓", true);
                if (ok) setConfirmCreate(false);
              }}>
              צור
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ProjectLinksPanel;
