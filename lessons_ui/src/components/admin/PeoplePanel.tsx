import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, Loader2, Pencil, Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import SearchableSelect from "@/components/ui/searchable-select";
import SearchableMultiSelect from "@/components/ui/searchable-multi-select";
import { roleLabels, PROJECT_STAGES, useUser } from "@/context/UserContext";
import type { MockUser, Project, UserRole } from "@/context/UserContext";
import { SUPABASE_URL } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";
import {
  filterPeople,
  GROUP_PROBLEM_LABELS,
  isGroupAccount,
  PERSON_PROBLEM_LABELS,
  type PeoplePayload,
  type PersonRow,
} from "@/lib/peopleSetup";

/**
 * ניהול משתמשים: every Shan-AI user and what the lessons module knows about
 * them — also people the old system never had (a new PM). An admin sets them up
 * BEFORE their first visit. Users are created in Shan-AI only; this page sets
 * the module fields (role, projects, referent groups). Server: /lessons/api/_people.
 */

const PERSON_ROLES: UserRole[] = ["project_manager", "admin", "viewer"];

const roleBadgeColors: Record<string, string> = {
  admin: "bg-accent/10 text-accent border-accent/20",
  project_manager: "bg-primary/10 text-primary border-primary/20",
  referent: "bg-info/10 text-info border-info/20",
  viewer: "bg-muted text-muted-foreground border-muted-foreground/20",
};

interface Props {
  users: MockUser[];
  projects: Project[];
  /** The module's edit dialog — used for a referent group's stages. */
  onEditGroup: (group: MockUser) => void;
}

const PeoplePanel = ({ users, projects, onEditGroup }: Props) => {
  const { reloadUsers } = useUser();
  const [data, setData] = useState<PeoplePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [editing, setEditing] = useState<PersonRow | null>(null);
  const [role, setRole] = useState<UserRole>("project_manager");
  const [picked, setPicked] = useState<string[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${SUPABASE_URL}/_people`, { credentials: "same-origin" });
      if (r.status === 403) {
        setError("ניהול משתמשים זמין למנהל מערכת של Shan-AI בלבד.");
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
      setError(null);
    } catch {
      setError("טעינת רשימת המשתמשים נכשלה. נסה לרענן.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(() => (data ? filterPeople(data.people, query, onlyProblems) : []), [data, query, onlyProblems]);
  const groupName = useMemo(() => new Map((data?.groups ?? []).map((g) => [g.id, g.name.trim()])), [data]);
  const managerOf = useMemo(() => {
    const m = new Map<number, string>();
    for (const u of users) if (u.role === "project_manager") for (const pid of u.assignedProjects) m.set(pid, u.name.trim());
    return m;
  }, [users]);

  const openEdit = (p: PersonRow) => {
    setEditing(p);
    const current = p.profile?.role as UserRole | undefined;
    // Not set up yet: start from the likeliest answer, not from "viewer".
    setRole(current && PERSON_ROLES.includes(current) && p.problems[0] !== "unset" ? current : "project_manager");
    setPicked((p.profile?.assigned_projects ?? []).map(String));
    setGroups(p.groups);
  };

  const save = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const r = await fetch(`${SUPABASE_URL}/_people`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shan_user_id: editing.id,
          role,
          assigned_projects: role === "project_manager" ? picked.map(Number) : [],
          groups,
        }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        toast({ title: body.message || "השמירה נכשלה", variant: "destructive" });
        return;
      }
      setData(body);
      setEditing(null);
      toast({ title: `${editing.username} הוגדר ✓` });
      await reloadUsers();
    } catch {
      toast({ title: "השמירה נכשלה", variant: "destructive" });
    } finally {
      setSaving(false);
    }
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

  const peopleOpen = data.people.filter((p) => p.problems.length > 0).length;
  const groupsOpen = data.groups.filter((g) => g.problems.length > 0).length;

  return (
    <div className="space-y-6">
      <Card className="border-0 shadow-sm">
        <CardHeader className="space-y-3">
          <CardTitle className="text-base font-heading flex items-center gap-2">
            <Users className="w-4 h-4" />
            משתמשי מערכת הלקחים ({data.people.length})
            {peopleOpen > 0 ? (
              <Badge variant="outline" className="bg-warning/10 text-warning border-warning/30 gap-1">
                <AlertTriangle className="w-3 h-3" />
                {peopleOpen} לא הוגדרו כראוי
              </Badge>
            ) : (
              <Badge variant="outline" className="bg-success/10 text-success border-success/30 gap-1">
                <CheckCircle className="w-3 h-3" />
                כולם מוגדרים
              </Badge>
            )}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            כל משתמשי Shan-AI. משתמש חדש (שלא היה במערכת הישנה) מוקם ב-Shan-AI (⚙️ מערכת ← 👥 משתמשים), ומוגדר כאן —
            אפשר עוד לפני הכניסה הראשונה שלו.
          </p>
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="חיפוש לפי שם, תפקיד או מייל"
              className="sm:max-w-xs"
            />
            <div className="flex items-center gap-2">
              <Switch id="only-problems" checked={onlyProblems} onCheckedChange={setOnlyProblems} />
              <Label htmlFor="only-problems" className="text-sm">רק מי שלא הוגדר</Label>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {visible.length === 0 && <p className="text-sm text-muted-foreground text-center py-6">אין משתמשים להצגה</p>}
          {visible.map((p) => {
            const groupAccount = isGroupAccount(p);
            const projectNames = (p.profile?.role === "project_manager" ? p.profile.assigned_projects : [])
              .map((pid) => projects.find((pr) => pr.id === pid)?.name)
              .filter(Boolean);
            return (
              <div
                key={p.id}
                className={`flex flex-col lg:flex-row lg:items-center justify-between gap-3 p-3 rounded-xl ${p.problems.length ? "bg-warning/5 ring-1 ring-warning/30" : "bg-muted/50"}`}
              >
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{p.username}</span>
                    {p.profile && p.problems[0] !== "unset" && (
                      <Badge variant="outline" className={roleBadgeColors[p.profile.role]}>
                        {groupAccount ? "חשבון קבוצת רפרנט" : roleLabels[p.profile.role as UserRole] ?? p.profile.role}
                      </Badge>
                    )}
                    {p.profile?.source === "imported" && !groupAccount && (
                      <Badge variant="outline" className="text-[10px]" title={`מקושר ל־${p.profile.name.trim()}`}>
                        מהמערכת הישנה
                      </Badge>
                    )}
                    {p.problems.map((code) => (
                      <Badge key={code} variant="outline" className="bg-warning/10 text-warning border-warning/30 gap-1">
                        <AlertTriangle className="w-3 h-3" />
                        {PERSON_PROBLEM_LABELS[code]}
                      </Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground truncate">
                    {p.job_title || "ללא תפקיד ב-Shan-AI"}
                    {p.email && ` · ${p.email}`}
                  </p>
                  {projectNames.length > 0 && (
                    <p className="text-xs text-muted-foreground truncate">פרויקטים: {projectNames.join(", ")}</p>
                  )}
                  {p.groups.length > 0 && (
                    <p className="text-xs text-muted-foreground truncate">
                      רפרנט ב: {p.groups.map((g) => groupName.get(g) ?? g).join(", ")}
                    </p>
                  )}
                </div>
                {!groupAccount && (
                  <Button variant="outline" size="sm" className="gap-1.5 shrink-0" onClick={() => openEdit(p)}>
                    <Pencil className="w-4 h-4" />
                    {p.problems[0] === "unset" ? "הגדר" : "ערוך"}
                  </Button>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading flex items-center gap-2">
            קבוצות רפרנט ({data.groups.length})
            {groupsOpen > 0 && (
              <Badge variant="outline" className="bg-warning/10 text-warning border-warning/30 gap-1">
                <AlertTriangle className="w-3 h-3" />
                {groupsOpen} לא מלאות
              </Badge>
            )}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            לקח שמנותב לקבוצה בלי חברים או בלי שלבים נתקע בלי מטפל. חברים מוסיפים בעריכת המשתמש (למעלה) או ב
            <a href={`${SUPABASE_URL}/_groups`} className="underline mx-1">דף הקבוצות</a>; שלבים — ✏️.
          </p>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.groups.map((g) => {
            const profile = users.find((u) => u.id === g.id);
            return (
              <div
                key={g.id}
                className={`flex flex-col lg:flex-row lg:items-center justify-between gap-3 p-3 rounded-xl ${g.problems.length ? "bg-warning/5 ring-1 ring-warning/30" : "bg-muted/50"}`}
              >
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{g.name.trim()}</span>
                    {g.problems.map((code) => (
                      <Badge key={code} variant="outline" className="bg-warning/10 text-warning border-warning/30 gap-1">
                        <AlertTriangle className="w-3 h-3" />
                        {GROUP_PROBLEM_LABELS[code]}
                      </Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground truncate">
                    חברים: {g.members.length ? g.members.map((m) => m.username.trim()).join(", ") : "—"}
                    {g.login_username && ` · חשבון כניסה: ${g.login_username}`}
                  </p>
                  {g.assigned_stage_indexes.length > 0 && (
                    <p className="text-xs text-muted-foreground truncate">
                      שלבים: {g.assigned_stage_indexes.map((si) => PROJECT_STAGES[si]).filter(Boolean).join(", ")}
                    </p>
                  )}
                </div>
                {profile && (
                  <Button variant="ghost" size="icon" className="h-8 w-8" title="שלבים וציוד" onClick={() => onEditGroup(profile)}>
                    <Pencil className="w-4 h-4" />
                  </Button>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent dir="rtl" className="max-w-lg">
          <DialogHeader>
            <DialogTitle>הגדרת {editing?.username}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>תפקיד במערכת הלקחים</Label>
              <SearchableSelect
                className="mt-1.5"
                value={role}
                onValueChange={(v) => setRole(v as UserRole)}
                options={PERSON_ROLES.map((r) => ({ value: r, label: roleLabels[r] }))}
              />
            </div>
            {role === "project_manager" && (
              <div>
                <Label>פרויקטים</Label>
                <SearchableMultiSelect
                  className="mt-1.5"
                  value={picked}
                  onChange={setPicked}
                  searchable
                  showSelectedBadges
                  placeholder="בחר פרויקטים"
                  options={projects.map((pr) => {
                    const holder = managerOf.get(pr.id);
                    const mine = editing?.profile && holder === editing.profile.name.trim();
                    return { value: String(pr.id), label: pr.name, hint: holder && !mine ? `אצל ${holder} — יועבר` : undefined };
                  })}
                />
                {picked.length === 0 && (
                  <p className="text-xs text-warning mt-1">מנהל פרויקט בלי פרויקטים לא יראה כלום — יסומן כלא מוגדר.</p>
                )}
              </div>
            )}
            {role === "admin" && <p className="text-xs text-muted-foreground">מנהל מערכת מקבל את כל הפרויקטים.</p>}
            <div>
              <Label>חבר בקבוצות רפרנט</Label>
              <SearchableMultiSelect
                className="mt-1.5"
                value={groups}
                onChange={setGroups}
                showSelectedBadges
                placeholder="ללא"
                options={data.groups.map((g) => ({ value: g.id, label: g.name.trim() }))}
              />
            </div>
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setEditing(null)} disabled={saving}>
              ביטול
            </Button>
            <Button onClick={save} disabled={saving} className="gap-1.5">
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              שמור
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default PeoplePanel;
