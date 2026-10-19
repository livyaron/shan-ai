import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, Download, FileSpreadsheet, FolderPlus, GitMerge, History, Link2, Link2Off, Loader2, MapPin, RefreshCw, Search, Star, Upload, UserCheck, Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import SearchableSelect from "@/components/ui/searchable-select";
import { SUPABASE_URL } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";
import {
  defaultPick,
  filterLinkRows,
  heldElsewhere,
  ISSUE_LABELS,
  MANAGER_PROBLEM_LABELS,
  MISSING_LABELS,
  managerNeedsAnswer,
  manualOptions,
  masterLabel,
  rowState,
  SEARCH_REASON_LABELS,
  visibleManagers,
  type HeldProject,
  type LinkRow,
  type LinkView,
  type SearchRow,
  type LinksPayload,
  type ManagersPayload,
} from "@/lib/projectLinks";

/**
 * קישור פרויקטים (PLAN-lessons-projects.md L1): each lessons project ↔ the
 * project in the weekly master file. Suggestions are name similarity only — a
 * hint; nothing is linked until the admin clicks. Duplicates are merged (the
 * lessons move, the duplicate is deleted). Once every lessons project is linked
 * or kept local, the file's remaining projects are created in one click.
 * Server: /lessons/api/_project_links (Shan-AI admin only).
 *
 * Work list (2026-10-19): by default only what still needs the admin is shown
 * (the server's link_issues); a handled project leaves the list at once and
 * comes back only with a new problem. "טופלו" shows the rest, for control.
 * What the file does not give (stage / type, a manager name) is completed here
 * or through the Excel table (download → fill → upload → preview → apply).
 */

interface ImportRow { row: number; label: string; value: string; status: string; status_label: string }
interface ImportPreview { rows: ImportRow[]; counts: Record<string, number> }

const ALL_CLEAR = "כל הפרויקטים סונכרנו וקושרו בהצלחה. אין פרויקטים הממתינים לטיפול.";

const ProjectLinksPanel = () => {
  const [data, setData] = useState<LinksPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<LinkView>("pending");
  const [heldPick, setHeldPick] = useState<Record<string, { stage?: string; project_type?: string }>>({});
  const [importFile, setImportFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<number, string>>({});
  const [mergeSource, setMergeSource] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [confirmMerge, setConfirmMerge] = useState(false);
  const [confirmCreate, setConfirmCreate] = useState(false);
  const [managers, setManagers] = useState<ManagersPayload | null>(null);
  const [mgrPick, setMgrPick] = useState<Record<string, string>>({});
  const [fileQuery, setFileQuery] = useState("");
  const [fileRows, setFileRows] = useState<SearchRow[] | null>(null);
  const [mgrRow, setMgrRow] = useState<number | null>(null);
  const [mgrUser, setMgrUser] = useState("");

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

  const loadManagers = useCallback(async () => {
    try {
      const r = await fetch(`${SUPABASE_URL}/_project_links/managers`, { credentials: "same-origin" });
      if (r.ok) setManagers(await r.json());
    } catch {
      /* the card stays hidden; the rest of the page works */
    }
  }, []);

  useEffect(() => {
    load();
    loadManagers();
  }, [load, loadManagers]);

  const act = async (key: string, body: Record<string, unknown>, done: string, reload = false): Promise<LinksPayload | null> => {
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
        return null;
      }
      setData(res);
      toast({ title: done });
      // Merge deletes a project and create adds many: the module's own lists must reload.
      if (reload) window.setTimeout(() => window.location.reload(), 800);
      return res as LinksPayload;
    } catch {
      toast({ title: "הפעולה נכשלה", variant: "destructive" });
      return null;
    } finally {
      setBusy(null);
    }
  };

  const searchFile = async () => {
    const q = fileQuery.trim();
    if (q.length < 2) return;
    try {
      const r = await fetch(`${SUPABASE_URL}/_project_links/search?q=${encodeURIComponent(q)}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      setFileRows((await r.json()).rows);
    } catch {
      toast({ title: "החיפוש נכשל", variant: "destructive" });
    }
  };

  const openManager = (row: LinkRow) => {
    setMgrRow(row.id);
    setMgrUser(row.manager_check?.suggest_user_id ? String(row.manager_check.suggest_user_id) : "");
  };

  /** The file project is held by another lessons project: offer to merge this one into it. */
  const startMerge = (r: LinkRow, target: number) => {
    setMergeSource(String(r.id));
    setMergeTarget(String(target));
    setConfirmMerge(true);
  };

  /** Link, then ask about the manager at once when the file and the module disagree. */
  const linkTo = async (r: LinkRow, identifier: string) => {
    const res = await act(`l${r.id}`, { action: "link", project_id: r.id, identifier }, `${r.name} קושר ✓`);
    const fresh = res?.projects.find((p) => p.id === r.id);
    if (fresh && managerNeedsAnswer(fresh)) openManager(fresh);
  };

  const visible = useMemo(() => (data ? filterLinkRows(data.projects, query, view) : []), [data, query, view]);

  /** Upload the completed table: preview first (nothing written), then apply the same file. */
  const sendTable = async (file: File, mode: "preview" | "apply") => {
    setBusy(`x${mode}`);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await fetch(`${SUPABASE_URL}/_project_links/completion?mode=${mode}`, {
        method: "POST", credentials: "same-origin", body: form,
      });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) {
        toast({ title: res.message || "קליטת הטבלה נכשלה", variant: "destructive" });
        return;
      }
      if (mode === "preview") {
        setPreview(res as ImportPreview);
        return;
      }
      setData(res.payload);
      loadManagers();
      setPreview(null);
      setImportFile(null);
      const failed = res.result?.failed?.length ?? 0;
      toast({
        title: `נקלטו ${res.result?.applied ?? 0} השלמות ✓`,
        description: failed ? `${failed} נכשלו — הם נשארים ברשימה` : undefined,
        variant: failed ? "destructive" : undefined,
      });
    } catch {
      toast({ title: "קליטת הטבלה נכשלה", variant: "destructive" });
    } finally {
      setBusy(null);
    }
  };

  const completeHeld = async (h: HeldProject) => {
    const pick = heldPick[h.identifier] ?? {};
    const res = await act(`h${h.identifier}`, { action: "complete", identifier: h.identifier, ...pick },
      `${(h.name ?? h.identifier).trim()} נשמר ✓`);
    if (res) setHeldPick((m) => { const n = { ...m }; delete n[h.identifier]; return n; });
  };

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
  const projectOption = (p: LinkRow) => ({
    value: String(p.id),
    label: `${p.name}${p.identifier ? " · מקושר לקובץ" : ""} (${p.lessons_count} לקחים)`,
  });
  const source = data.projects.find((p) => String(p.id) === mergeSource);
  const target = data.projects.find((p) => String(p.id) === mergeTarget);

  return (
    <div className="space-y-6">
      <Card className="border-0 shadow-sm">
        <CardHeader className="space-y-3">
          <CardTitle className="text-base font-heading flex flex-wrap items-center gap-2">
            <Link2 className="w-4 h-4" />
            קישור פרויקטים לקובץ המאסטר
            {s.all_clear ? (
              <Badge variant="outline" className="bg-success/10 text-success border-success/30 gap-1">
                <CheckCircle className="w-3 h-3" />
                הכל מטופל
              </Badge>
            ) : (
              <Badge variant="outline" className="bg-warning/10 text-warning border-warning/30 gap-1">
                <AlertTriangle className="w-3 h-3" />
                {s.pending + s.held.length + s.managers_open} ממתינים לטיפול
              </Badge>
            )}
          </CardTitle>
          {!s.engine_ok && (
            <div className="text-xs text-warning flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" />
              מנוע הדפוסים לא זמין כרגע — רמות הסיכון לא עודכנו ופרויקטים חדשים לא נוצרים עד שיחזור. לא שונה דבר.
            </div>
          )}
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
          <div className="inline-flex rounded-lg bg-muted p-1 w-fit" role="tablist">
            {([["pending", `ממתינים לטיפול (${s.pending})`], ["handled", `טופלו (${s.handled})`]] as const).map(([v, label]) => (
              <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setView(v)}
                className={`px-3 py-1.5 text-sm rounded-md transition-colors ${view === v ? "bg-background shadow-sm font-medium" : "text-muted-foreground hover:text-foreground"}`}>
                {label}
              </button>
            ))}
          </div>
          {view === "handled" && (
            <p className="text-xs text-muted-foreground">
              לבקרה בלבד: פרויקטים שקושרו (או סומנו מקומיים) ואין להם שום דבר פתוח. הם לא יחזרו לרשימת העבודה, אלא אם
              יתגלה שינוי חדש — למשל הקובץ ימנה מנה״פ אחר.
            </p>
          )}
          {view === "pending" && (
            <p className="text-xs text-muted-foreground">
              לכל פרויקט לקחים שלא קושר מוצגות עד 3 הצעות מקובץ המאסטר. ⭐ = המלצה ברורה (מזהה זהה בשם, או אותו מנה״פ)
              והיא מסומנת מראש — שום דבר לא מקושר עד שלוחצים "קשר". אחוז = דמיון שמות בלבד. "מקומי" = פרויקט שלא קיים בקובץ.
            </p>
          )}
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="חיפוש לפי שם או מנה״פ"
            className="sm:max-w-xs" />
        </CardHeader>
        <CardContent className="space-y-2">
          {view === "pending" && s.all_clear && (
            <div className="rounded-xl bg-success/10 ring-1 ring-success/30 p-4 text-center text-sm text-success flex items-center justify-center gap-2">
              <CheckCircle className="w-4 h-4" />
              {ALL_CLEAR}
            </div>
          )}
          {visible.length === 0 && !(view === "pending" && s.all_clear) && (
            <p className="text-sm text-muted-foreground text-center py-6">
              {view === "pending" ? "אין פרויקטי לקחים הממתינים לטיפול ברשימה הזו" : "אין פרויקטים להצגה"}
            </p>
          )}
          {visible.map((r) => {
            const state = rowState(r);
            const options = manualOptions(data.masters, r.id);
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
                  {state === "linked" && r.manager_check && (() => {
                    const mc = r.manager_check;
                    if (mc.status === "match") {
                      return <Badge variant="outline" className="text-success border-success/40 gap-1"><UserCheck className="w-3 h-3" />מנה״פ תואם לקובץ</Badge>;
                    }
                    if (mc.status === "kept") {
                      return <Badge variant="outline" className="text-muted-foreground">מנה״פ נשמר ידנית (בקובץ: {mc.file_manager})</Badge>;
                    }
                    if (mc.status === "no_name") {
                      if ((r.issues ?? []).includes("manager_placeholder")) {
                        return (
                          <button type="button" onClick={() => openManager(r)}
                            className="inline-flex items-center gap-1 rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-xs text-warning hover:bg-warning/20">
                            <AlertTriangle className="w-3 h-3" />
                            אין מנה״פ בקובץ — הפרויקט אצל "טרם שויך". בחר מנה״פ
                          </button>
                        );
                      }
                      return <Badge variant="outline" className="text-muted-foreground">אין מנה״פ בקובץ</Badge>;
                    }
                    return (
                      <button type="button" onClick={() => openManager(r)}
                        className="inline-flex items-center gap-1 rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-xs text-warning hover:bg-warning/20">
                        <AlertTriangle className="w-3 h-3" />
                        {mc.status === "differs" ? "מנה״פ שונה מהקובץ — בדוק" : "מנה״פ הקובץ לא מזוהה — בדוק"}
                      </button>
                    );
                  })()}
                  {r.sync_error && (
                    <span className="inline-flex flex-wrap items-center gap-1 rounded-full border border-destructive/40 bg-destructive/10 px-2 py-0.5 text-xs text-destructive"
                      title={r.sync_error}>
                      <AlertTriangle className="w-3 h-3" />
                      {ISSUE_LABELS.sync_error}: {r.sync_error.slice(0, 80)}
                      <button type="button" className="underline" disabled={busy !== null}
                        onClick={() => act("sync", { action: "sync_now" }, "הסנכרון הושלם ✓")}>נסה שוב</button>
                    </span>
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

                {state === "open" && r.suggestions.length > 0 && (() => {
                  const choice = picked[r.id] ?? defaultPick(r);
                  return (
                    <div className="space-y-1.5">
                      <div className="text-xs text-muted-foreground">הצעות מקובץ המאסטר — בחר אחת ולחץ "קשר":</div>
                      {r.suggestions.map((sg) => {
                        const on = choice === sg.identifier;
                        if (heldElsewhere(sg, r.id) && sg.linked_to != null) {
                          const holder = sg.linked_to;
                          return (
                            <button key={sg.identifier} type="button" disabled={busy !== null}
                              onClick={() => startMerge(r, holder)}
                              className="w-full text-start flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-muted-foreground/40 bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted">
                              <GitMerge className="w-3.5 h-3.5" />
                              <span className="font-medium">{masterLabel(sg)}</span>
                              <span>({sg.score}%)</span>
                              <Badge variant="outline" className="h-5 px-1.5">
                                כבר מקושר ל"{(sg.linked_name ?? "").trim()}" — כפילות? לחץ לאיחוד
                              </Badge>
                            </button>
                          );
                        }
                        return (
                          <button key={sg.identifier} type="button" disabled={busy !== null}
                            onClick={() => setPicked((m) => ({ ...m, [r.id]: on ? "" : sg.identifier }))}
                            className={`w-full text-start flex flex-wrap items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-colors ${on ? "border-primary bg-primary/10 ring-1 ring-primary" : "border-border bg-background hover:bg-muted"}`}>
                            <span className={`w-3.5 h-3.5 rounded-full border ${on ? "border-primary bg-primary" : "border-muted-foreground/50"}`} />
                            {sg.recommended && (
                              <Badge className="bg-success text-success-foreground gap-1 h-5 px-1.5">
                                <Star className="w-3 h-3" />
                                ההמלצה
                              </Badge>
                            )}
                            <span className="font-medium">{masterLabel(sg)}</span>
                            <span className="text-muted-foreground">({sg.score}%)</span>
                            {sg.id_match && <Badge variant="outline" className="h-5 px-1.5 text-success border-success/40">✓ מזהה זהה</Badge>}
                            {sg.manager_match && <Badge variant="outline" className="h-5 px-1.5 text-success border-success/40">✓ אותו מנה״פ</Badge>}
                          </button>
                        );
                      })}
                      <Button size="sm" className="gap-1" disabled={!choice || busy !== null}
                        onClick={() => linkTo(r, choice)}>
                        <Link2 className="w-3.5 h-3.5" />
                        קשר
                      </Button>
                    </div>
                  );
                })()}

                <div className="flex flex-wrap items-center gap-2">
                  {state === "open" && (
                    <>
                      <SearchableSelect
                        value=""
                        onValueChange={(v) => {
                          const m = data.masters.find((x) => x.identifier === v);
                          if (m && heldElsewhere(m, r.id) && m.linked_to != null) startMerge(r, m.linked_to);
                          else linkTo(r, v);
                        }}
                        options={options}
                        placeholder="אף הצעה לא נכונה? בחר ידנית מהקובץ…"
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
                  {busy !== null && busy.slice(1) === String(r.id) && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      {(() => {
        const ls = data.last_sync;
        return (
          <Card className="border-0 shadow-sm">
            <CardHeader className="space-y-2">
              <CardTitle className="text-base font-heading flex flex-wrap items-center gap-2">
                <History className="w-4 h-4" />
                סנכרון אחרון מול קובץ המאסטר
                {ls?.at && (
                  <span className="text-xs font-normal text-muted-foreground">
                    {new Date(ls.at).toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" })}
                    {ls.actor ? ` · ${ls.actor}` : ""}
                  </span>
                )}
              </CardTitle>
              <div>
                <Button size="sm" variant="outline" className="gap-1.5" disabled={busy !== null}
                  onClick={async () => {
                    const res = await act("sync", { action: "sync_now" }, "הסנכרון הושלם ✓");
                    if (res) loadManagers();
                  }}>
                  <RefreshCw className="w-3.5 h-3.5" />
                  סנכרן עכשיו מול הקובץ
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {!ls ? (
                <p className="text-muted-foreground">עדיין לא נרשם סנכרון. הדוח יופיע אחרי ההעלאה הבאה של קובץ המאסטר או אחרי "סנכרן עכשיו".</p>
              ) : (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-center">
                    {[
                      ["נוצרו", ls.created.length, ""],
                      ["מקושרים ותקינים", ls.linked_ok, "text-success"],
                      ["ממתינים להשלמת נתונים", ls.awaiting_data.length, ls.awaiting_data.length ? "text-warning" : ""],
                      ["ממתינים לאישור מנה״פ", ls.awaiting_manager, ls.awaiting_manager ? "text-warning" : ""],
                      ["נכשלו", ls.failed.length, ls.failed.length ? "text-destructive" : ""],
                    ].map(([label, n, cls]) => (
                      <div key={label as string} className="rounded-xl bg-muted/50 p-2">
                        <div className={`text-lg font-bold ${cls}`}>{n}</div>
                        <div className="text-xs text-muted-foreground">{label}</div>
                      </div>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    עודכנו {ls.updated} פרויקטים מהקובץ{ls.managers_filled ? ` · ${ls.managers_filled} קיבלו מנה״פ במקום "טרם שויך"` : ""}
                    {ls.inactive ? ` · ${ls.inactive} יצאו מהקובץ (סומנו לא פעילים, לא נמחקו)` : ""}
                    {ls.waiting_duplicate?.length ? ` · ${ls.waiting_duplicate.length} ממתינים להחלטה על פרויקט לקחים דומה` : ""}
                    {ls.excluded_removed?.length ? ` · ${ls.excluded_removed.length} פרויקטי ניידות הוסרו מהלקחים` : ""}
                    {!ls.engine_ok ? " · מנוע הדפוסים לא היה זמין — סיכון לא עודכן ולא נוצרו פרויקטים" : ""}
                  </p>
                  {(ls.excluded_kept?.length ?? 0) > 0 && (
                    <div className="rounded-xl bg-warning/5 ring-1 ring-warning/30 p-3 space-y-1">
                      <div className="text-xs font-medium">פרויקטי ניידות שנשארו כי יש עליהם לקחים — לא נמחקים אוטומטית:</div>
                      {ls.excluded_kept.map((k) => (
                        <div key={k.id} className="text-xs">{k.name.trim()} — {k.lessons} לקחים, {k.implementations} הפצות</div>
                      ))}
                    </div>
                  )}
                  {ls.failed.length > 0 && (
                    <div className="rounded-xl bg-destructive/5 ring-1 ring-destructive/30 p-3 space-y-1">
                      {ls.failed.map((f, i) => (
                        <div key={i} className="text-xs">{(f.name ?? f.identifier ?? f.id ?? "").toString().trim()} — {f.error}</div>
                      ))}
                    </div>
                  )}
                  {ls.created.length > 0 && (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground">הפרויקטים שנוצרו ({ls.created.length})</summary>
                      <div className="mt-1 space-y-0.5">
                        {ls.created.map((c) => <div key={c.identifier}>{c.identifier} · {(c.name ?? "").trim()}</div>)}
                      </div>
                    </details>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        );
      })()}

      {view === "pending" && (s.held.length > 0 || s.managers_open > 0 || data.projects.some((p) => (p.issues ?? []).includes("manager_placeholder"))) && (
        <Card className="border-0 shadow-sm">
          <CardHeader className="space-y-2">
            <CardTitle className="text-base font-heading flex flex-wrap items-center gap-2">
              <FileSpreadsheet className="w-4 h-4" />
              פרויקטים להשלמת נתונים
              {s.held.length > 0 && (
                <Badge variant="outline" className="bg-warning/10 text-warning border-warning/30">{s.held.length} ממתינים ליצירה</Badge>
              )}
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              רק מה שקובץ המאסטר לא נותן ואי אפשר לקבוע בלי אדם: שלב או סוג פרויקט לא מוכרים, ושמות מנה״פ שלא קושרו
              למשתמש. מוצגים רק השדות החסרים — שום ערך לא ממולא בברירת מחדל. אפשר להשלים כאן, או בטבלת Excel: להוריד,
              למלא את העמודה הצהובה ולהעלות בחזרה (קודם תצוגה מקדימה, ורק אז החלה).
            </p>
            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm" variant="outline" className="gap-1.5">
                <a href={`${SUPABASE_URL}/_project_links/completion.xlsx`} download>
                  <Download className="w-3.5 h-3.5" />
                  הורד טבלה להשלמה (Excel)
                </a>
              </Button>
              <Button asChild size="sm" variant="outline" className="gap-1.5" disabled={busy !== null}>
                <label className="cursor-pointer">
                  <Upload className="w-3.5 h-3.5" />
                  העלה טבלה מושלמת
                  <input type="file" accept=".xlsx" className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      e.target.value = "";
                      if (f) { setImportFile(f); sendTable(f, "preview"); }
                    }} />
                </label>
              </Button>
              {busy?.startsWith("x") && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground self-center" />}
            </div>
          </CardHeader>
          {s.held.length > 0 && (
            <CardContent className="space-y-2">
              {s.held.map((h) => {
                const pick = heldPick[h.identifier] ?? {};
                const ready = h.missing.some((f) => Boolean(pick[f]));
                return (
                  <div key={h.identifier} className="flex flex-col lg:flex-row lg:items-center gap-2 p-3 rounded-xl bg-warning/5 ring-1 ring-warning/30 text-sm">
                    <div className="min-w-0 flex-1">
                      <div className="font-medium">{h.identifier} · {(h.name ?? "").trim() || "—"}</div>
                      <div className="text-xs text-muted-foreground">
                        {h.manager ? `מנה״פ בקובץ: ${h.manager.trim()} · ` : ""}
                        חסר: {h.missing.map((f) => MISSING_LABELS[f]).join(", ")}
                        {h.missing.includes("stage") && ` (בקובץ: "${h.stage || "ריק"}")`}
                        {h.missing.includes("project_type") && ` (סוג בקובץ: "${h.project_type || "ריק"}")`}
                      </div>
                    </div>
                    {h.missing.includes("stage") && (
                      <SearchableSelect value={pick.stage ?? ""} searchable placeholder="בחר שלב…" className="w-full lg:w-52"
                        onValueChange={(v) => setHeldPick((m) => ({ ...m, [h.identifier]: { ...m[h.identifier], stage: v } }))}
                        options={data.stage_options.map((o) => ({ value: o, label: o }))} />
                    )}
                    {h.missing.includes("project_type") && (
                      <SearchableSelect value={pick.project_type ?? ""} placeholder="בחר סוג…" className="w-full lg:w-40"
                        onValueChange={(v) => setHeldPick((m) => ({ ...m, [h.identifier]: { ...m[h.identifier], project_type: v } }))}
                        options={data.type_options} />
                    )}
                    <Button size="sm" disabled={!ready || busy !== null} onClick={() => completeHeld(h)}>
                      שמירה והמשך סנכרון
                    </Button>
                  </div>
                );
              })}
            </CardContent>
          )}
        </Card>
      )}

      {managers && visibleManagers(managers.rows, view).length > 0 && (
        <Card className="border-0 shadow-sm">
          <CardHeader className="space-y-2">
            <CardTitle className="text-base font-heading flex flex-wrap items-center gap-2">
              <Users className="w-4 h-4" />
              {view === "pending"
                ? `שמות מנה״פ בקובץ שעדיין לא קושרו (${managers.open})`
                : `שמות מנה״פ בקובץ שקושרו (${managers.rows.length - managers.open})`}
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              כל שם שמופיע בעמודת מנה״פ בקובץ, ומי הוא ב-Shan-AI. ההצעה היא רמז בלבד — שום דבר לא נשמר עד שלוחצים "אשר".
              אחרי "אשר" הסנכרון רץ מיד: פרויקטים של השם הזה שנמצאים אצל "טרם שויך" עוברים אליו. פרויקט שכבר יש לו מנה״פ
              אחר לא מוחלף — הוא מופיע ברשימה כ"ממתין לאישור". משתמש שאינו מנה״פ במערכת הלקחים יוגדר כמנה״פ.
            </p>
          </CardHeader>
          <CardContent className="space-y-2">
            {visibleManagers(managers.rows, view).map((m) => {
              const pick = mgrPick[m.name] ?? (m.user_id ? String(m.user_id) : m.hint_user_id ? String(m.hint_user_id) : "");
              return (
                <div key={m.name}
                  className={`flex flex-col lg:flex-row lg:items-center justify-between gap-2 p-2.5 rounded-xl text-sm ${m.problem ? "bg-warning/5 ring-1 ring-warning/30" : "bg-muted/50"}`}>
                  <div className="min-w-0">
                    <span className="font-medium">{m.name.trim()}</span>
                    <span className="text-xs text-muted-foreground"> · {m.projects} פרויקטים</span>
                    <div className="text-xs">
                      {m.problem ? (
                        <span className="text-warning">{MANAGER_PROBLEM_LABELS[m.problem]}{m.username ? ` (${m.username})` : ""}</span>
                      ) : (
                        <span className="text-success">✓ {m.username}</span>
                      )}
                      {!m.user_id && m.hint_user_id && <span className="text-muted-foreground"> · הצעה מסומנת מראש</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <SearchableSelect value={pick} searchable placeholder="בחר משתמש…" className="w-full sm:w-56"
                      onValueChange={(v) => setMgrPick((p) => ({ ...p, [m.name]: v }))}
                      options={managers.users.map((u) => ({ value: String(u.id), label: u.username }))} />
                    <Button size="sm" disabled={!pick || busy !== null || (!m.problem && pick === String(m.user_id))}
                      onClick={async () => {
                        const res = await act(`a${m.name}`, { action: "alias", alias: m.name, user_id: Number(pick) },
                          `${m.name.trim()} קושר ✓`);
                        if (res) loadManagers();
                      }}>
                      אשר
                    </Button>
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading flex items-center gap-2">
            <Search className="w-4 h-4" />
            חיפוש בקובץ המאסטר
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            פרויקט לא מופיע בהצעות? חפש לפי שם, מקום, מזהה או מנה״פ — החיפוש עובר על כל הפרויקטים בקובץ, כולל לא פעילים,
            ואומר למה כל אחד מוצע או לא.
          </p>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); searchFile(); }}>
            <Input value={fileQuery} onChange={(e) => setFileQuery(e.target.value)} placeholder="למשל: גבעת אורן" className="sm:max-w-xs" />
            <Button type="submit" variant="outline" className="gap-1.5" disabled={fileQuery.trim().length < 2}>
              <Search className="w-4 h-4" />
              חפש
            </Button>
          </form>
          {fileRows !== null && fileRows.length === 0 && <p className="text-muted-foreground">לא נמצא בקובץ.</p>}
          {fileRows?.map((f) => (
            <div key={f.identifier} className={`rounded-lg p-2 text-xs space-y-0.5 ${f.reason === "free" ? "bg-success/5 ring-1 ring-success/30" : "bg-muted/50"}`}>
              <div className="font-medium">{f.identifier} · {f.name}</div>
              <div className="text-muted-foreground">{[f.manager && `מנה״פ ${f.manager}`, f.stage].filter(Boolean).join(" · ")}</div>
              <div>{SEARCH_REASON_LABELS[f.reason]}{f.reason === "linked" && f.linked_name ? `: ${f.linked_name}` : ""}</div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading flex items-center gap-2">
            <GitMerge className="w-4 h-4" />
            איחוד כפילויות בתוך מערכת הלקחים
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            לא קשור לקובץ המאסטר. רק כשאותו פרויקט נרשם פעמיים במערכת הלקחים בשמות שונים: הלקחים והמלצות היישום של
            הכפול עוברים לפרויקט שנשאר, והכפול נמחק. לא ניתן לבטל.
          </p>
          <div className="grid sm:grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">הפרויקט הכפול (יימחק)</Label>
              <SearchableSelect value={mergeSource} onValueChange={setMergeSource} searchable
                options={data.projects.filter((p) => String(p.id) !== mergeTarget).map(projectOption)} placeholder="בחר…" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">הפרויקט שנשאר</Label>
              <SearchableSelect value={mergeTarget} onValueChange={setMergeTarget} searchable
                options={data.projects.filter((p) => String(p.id) !== mergeSource).map(projectOption)} placeholder="בחר…" />
            </div>
          </div>
          <Button variant="outline" className="gap-1.5" disabled={!source || !target || busy !== null}
            onClick={() => setConfirmMerge(true)}>
            <GitMerge className="w-4 h-4" />
            אחד
          </Button>
        </CardContent>
      </Card>

      {view === "pending" && (s.to_create > 0 || s.waiting_duplicate.length > 0) && (
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base font-heading flex items-center gap-2">
              <FolderPlus className="w-4 h-4" />
              יצירת הפרויקטים החסרים מהקובץ ({s.to_create})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="text-muted-foreground">
              כל פרויקט פעיל בקובץ שאין לו פרויקט לקחים מקושר, ושיש לו שלב וסוג מוכרים, ייווצר עם שם, שלב, סוג, סיכון
              ומנה״פ מהקובץ. פרויקטים שחסר להם שלב או סוג מחכים ב"פרויקטים להשלמת נתונים" ולא נוצרים עם ערך מומצא.
              פרויקטי ניידות לא נכללים במערכת הלקחים. פרויקטים חדשים בקובץ נוצרים מעצמם בכל העלאה ובכל "סנכרן עכשיו".
            </p>
            {s.no_manager > 0 && (
              <p className="text-warning">
                {s.no_manager} פרויקטים ייווצרו עם מנה״פ "טרם שויך" — השם שלהם בקובץ לא מקושר למשתמש. הם יישארו ברשימה עד
                שהשם יקושר.
              </p>
            )}
            {s.to_create > 0 && (
              <Button className="gap-1.5" disabled={!data.can_create || busy !== null} onClick={() => setConfirmCreate(true)}>
                <FolderPlus className="w-4 h-4" />
                צור {s.to_create} פרויקטים
              </Button>
            )}
            {s.waiting_duplicate.length > 0 && (
              <div className="rounded-xl bg-muted/50 p-3 space-y-1">
                <div className="text-xs font-medium">
                  ממתינים להחלטה ({s.waiting_duplicate.length}) — דומים לפרויקט לקחים שעוד לא קושר, ולכן לא נוצרים לידו כדי שלא
                  תהיה כפילות. ייווצרו מעצמם ברגע שתקשר את פרויקט הלקחים הדומה או תסמן אותו "מקומי":
                </div>
                {s.waiting_duplicate.map((w) => (
                  <div key={w.identifier} className="text-xs">{w.identifier} · {(w.name ?? "").trim()} ← דומה ל"{w.candidate.trim()}"</div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {(() => {
        const r = data.projects.find((p) => p.id === mgrRow);
        const mc = r?.manager_check;
        if (!r || !mc) return null;
        const close = () => setMgrRow(null);
        const answer = async (mode: "file" | "user" | "keep") => {
          const body: Record<string, unknown> = { action: "manager", project_id: r.id, mode };
          if (mode === "user") body.user_id = Number(mgrUser);
          const ok = await act(`g${r.id}`, body, mode === "keep" ? "המנה״פ נשאר כמו שהוא" : "המנה״פ עודכן ✓");
          if (ok) close();
        };
        return (
          <Dialog open onOpenChange={(o) => !o && close()}>
            <DialogContent dir="rtl">
              <DialogHeader>
                <DialogTitle>מנה״פ של "{r.name}"</DialogTitle>
              </DialogHeader>
              <div className="space-y-3 text-sm">
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-lg bg-muted/50 p-2">
                    <div className="text-xs text-muted-foreground">בקובץ המאסטר</div>
                    <div className="font-medium">{mc.file_manager ?? "—"}</div>
                  </div>
                  <div className="rounded-lg bg-muted/50 p-2">
                    <div className="text-xs text-muted-foreground">כרגע במערכת הלקחים</div>
                    <div className="font-medium">{mc.current.name?.trim() || mc.current.id}</div>
                  </div>
                </div>
                {mc.status === "differs" && mc.file_profile && (
                  <Button className="w-full gap-1.5" disabled={busy !== null} onClick={() => answer("file")}>
                    <UserCheck className="w-4 h-4" />
                    העבר ל-{mc.file_profile.name?.trim() || mc.file_profile.id} (לפי הקובץ)
                  </Button>
                )}
                <div className="space-y-1.5 rounded-lg border p-3">
                  <div className="text-xs">
                    {mc.status === "no_name" ? "אין מנה״פ בקובץ לפרויקט הזה. בחר את מנה״פ שלו:" : mc.status === "unresolved"
                      ? `מי זה "${mc.file_manager}"? ${mc.problem && mc.problem !== "no_name" ? `(${MANAGER_PROBLEM_LABELS[mc.problem]})` : ""}`
                      : `"${mc.file_manager}" מקושר לאדם הלא נכון? בחר את הנכון:`}
                  </div>
                  <SearchableSelect value={mgrUser} onValueChange={setMgrUser} searchable placeholder="בחר משתמש Shan-AI…"
                    options={data.users.map((u) => ({ value: String(u.id), label: u.username }))} />
                  <Button variant={mc.status === "unresolved" ? "default" : "outline"} className="w-full" disabled={!mgrUser || busy !== null}
                    onClick={() => answer("user")}>
                    זה הוא — קבע כמנה״פ של הפרויקט
                  </Button>
                  {mc.file_manager && (
                    <p className="text-[11px] text-muted-foreground">
                      הקישור "{mc.file_manager}" ← המשתמש נשמר גם לדף הדפוסים. משתמש שאינו מנה״פ במערכת הלקחים יוגדר כמנה״פ.
                    </p>
                  )}
                </div>
              </div>
              {mc.file_manager && (
                <DialogFooter>
                  <Button variant="ghost" disabled={busy !== null} onClick={() => answer("keep")}>
                    השאר את {mc.current.name?.trim() || "המנה״פ הנוכחי"}
                  </Button>
                </DialogFooter>
              )}
            </DialogContent>
          </Dialog>
        );
      })()}

      <Dialog open={preview !== null} onOpenChange={(o) => { if (!o) { setPreview(null); setImportFile(null); } }}>
        <DialogContent dir="rtl" className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>תצוגה מקדימה — טבלת ההשלמה</DialogTitle>
          </DialogHeader>
          {preview && (
            <div className="space-y-3 text-sm">
              <p className="text-muted-foreground">
                עדיין לא נשמר כלום. {preview.counts.ok ?? 0} שורות יעודכנו · {preview.counts.empty ?? 0} שורות ריקות לא נוגעות בכלום
                {(preview.rows.length - (preview.counts.ok ?? 0)) > 0 ? ` · ${preview.rows.length - (preview.counts.ok ?? 0)} ידולגו או יידחו` : ""}.
              </p>
              <div className="max-h-80 overflow-y-auto rounded-lg border">
                <table className="w-full text-xs">
                  <thead className="bg-muted/60 sticky top-0">
                    <tr><th className="p-1.5 text-start">שורה</th><th className="p-1.5 text-start">פריט</th><th className="p-1.5 text-start">ערך</th><th className="p-1.5 text-start">מה יקרה</th></tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((r) => (
                      <tr key={r.row} className="border-t">
                        <td className="p-1.5">{r.row}</td>
                        <td className="p-1.5">{r.label}</td>
                        <td className="p-1.5">{r.value}</td>
                        <td className={`p-1.5 ${r.status === "ok" ? "text-success" : r.status === "closed" ? "text-muted-foreground" : "text-destructive"}`}>{r.status_label}</td>
                      </tr>
                    ))}
                    {preview.rows.length === 0 && (
                      <tr><td colSpan={4} className="p-3 text-center text-muted-foreground">לא מולא אף ערך בעמודה הצהובה</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button disabled={!preview?.counts.ok || !importFile || busy !== null}
              onClick={() => importFile && sendTable(importFile, "apply")}>
              {busy === "xapply" ? <Loader2 className="w-4 h-4 animate-spin" /> : `החל ${preview?.counts.ok ?? 0} השלמות`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirmMerge} onOpenChange={setConfirmMerge}>
        <DialogContent dir="rtl">
          <DialogHeader>
            <DialogTitle>לאחד את "{source?.name}" לתוך "{target?.name}"?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {source?.lessons_count ?? 0} הלקחים והמלצות היישום של "{source?.name}" יעברו ל-"{target?.name}", ו-"{source?.name}"
            יימחק. לא ניתן לבטל.
          </p>
          <DialogFooter>
            <Button variant="destructive" disabled={!source || !target || busy !== null}
              onClick={async () => {
                if (!source || !target) return;
                const ok = await act(`m${source.id}`, { action: "merge", source_id: source.id, target_id: target.id },
                  "האיחוד הושלם ✓", true);
                if (ok) setConfirmMerge(false);
              }}>
              אחד ומחק את הכפול
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
