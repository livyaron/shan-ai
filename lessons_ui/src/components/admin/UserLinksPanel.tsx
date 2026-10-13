import { useCallback, useEffect, useMemo, useState } from "react";
import { Link2, Link2Off, Loader2, Pencil, UserPlus } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import SearchableSelect from "@/components/ui/searchable-select";
import { roleLabels, PROJECT_STAGES } from "@/context/UserContext";
import type { MockUser, Project, UserRole } from "@/context/UserContext";
import { SUPABASE_URL } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";

/**
 * קישור משתמשים: every existing module profile (a person or a role imported
 * from the old system) is linked to the Shan-AI user who now signs in as it. The
 * Shan-AI user inherits the profile's role, projects and history.
 *
 * Users are created in Shan-AI only (⚙️ מערכת → 👥 משתמשים); the module never
 * creates or deletes people. Links are picked from the Shan-AI list — never
 * matched by name. Server: /lessons/api/_links (Shan-AI admin only).
 */

interface LinkProfile {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  shan_user_id: number | null;
  shan_username: string | null;
}

interface ShanUser {
  id: number;
  username: string;
  job_title: string | null;
  linked_profile: { id: string; name: string; role: UserRole } | null;
  visited: boolean;
}

interface Payload {
  profiles: LinkProfile[];
  users: ShanUser[];
}

const roleBadgeColors: Record<string, string> = {
  admin: "bg-accent/10 text-accent border-accent/20",
  project_manager: "bg-primary/10 text-primary border-primary/20",
  referent: "bg-info/10 text-info border-info/20",
  viewer: "bg-muted text-muted-foreground border-muted-foreground/20",
};

const userLabel = (u: ShanUser) => `${u.username.trim()}${u.job_title ? ` — ${u.job_title}` : ""}`;

interface Props {
  /** The module's profiles (for the edit dialog: role, projects, stages). */
  users: MockUser[];
  projects: Project[];
  onEdit: (user: MockUser) => void;
}

const UserLinksPanel = ({ users, projects, onEdit }: Props) => {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${SUPABASE_URL}/_links`, { credentials: "same-origin" });
      if (r.status === 403) {
        setError("קישור משתמשים זמין למנהל מערכת של Shan-AI בלבד.");
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
      setError(null);
    } catch {
      setError("טעינת רשימת הקישורים נכשלה. נסה לרענן.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = async (profileId: string, shanUserId: number | null) => {
    setBusy(profileId);
    try {
      const r = await fetch(`${SUPABASE_URL}/_links`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profileId, shan_user_id: shanUserId }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        toast({ title: body.message || "הפעולה נכשלה", variant: "destructive" });
        return;
      }
      setData(body);
      toast({ title: shanUserId === null ? "הקישור נותק ✓" : "קושר ✓" });
    } catch {
      toast({ title: "הפעולה נכשלה", variant: "destructive" });
    } finally {
      setBusy(null);
    }
  };

  // Shan-AI users not yet linked to any existing profile — the new names.
  const waiting = useMemo(() => (data ? data.users.filter((u) => !u.linked_profile) : []), [data]);
  const freeProfiles = useMemo(() => (data ? data.profiles.filter((p) => p.shan_user_id === null) : []), [data]);
  const [pick, setPick] = useState<Record<number, string>>({});

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

  const userOptions = [
    { value: "", label: "— לא מקושר —" },
    ...data.users.map((u) => ({
      value: String(u.id),
      label: userLabel(u),
      hint: u.linked_profile ? `מקושר ל־${u.linked_profile.name.trim()}` : undefined,
    })),
  ];

  return (
    <div className="space-y-6">
      {waiting.length > 0 && (
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base font-heading flex items-center gap-2">
              <UserPlus className="w-4 h-4" />
              משתמשי Shan-AI שעדיין לא מקושרים ({waiting.length})
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              כל עוד משתמש לא מקושר, הוא נכנס למערכת הלקחים בצפייה בלבד. קשר אותו לשם/תפקיד קיים כדי שיקבל את
              התפקיד, הפרויקטים וההיסטוריה שלו.
            </p>
          </CardHeader>
          <CardContent className="space-y-2">
            {waiting.map((u) => (
              <div key={u.id} className="flex flex-col sm:flex-row sm:items-center gap-2 p-3 rounded-xl bg-muted/50">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{u.username.trim()}</p>
                  <p className="text-xs text-muted-foreground truncate">
                    {u.job_title || "ללא תפקיד ב-Shan-AI"}
                    {u.visited && " · כבר נכנס למערכת"}
                  </p>
                </div>
                <SearchableSelect
                  className="sm:w-72"
                  value={pick[u.id] ?? ""}
                  onValueChange={(v) => setPick((prev) => ({ ...prev, [u.id]: v }))}
                  placeholder="בחר שם/תפקיד קיים"
                  searchable
                  options={freeProfiles.map((p) => ({ value: p.id, label: `${p.name.trim()} (${roleLabels[p.role] ?? p.role})` }))}
                  emptyMessage="אין פרופילים פנויים"
                />
                {(() => {
                  // Someone with no existing profile at all: give the viewer row
                  // they got on their first visit a role instead.
                  const own = users.find((x) => x.id === `s${u.id}`);
                  return own ? (
                    <Button variant="ghost" size="icon" className="h-8 w-8" title="הגדר תפקיד בלי קישור" onClick={() => onEdit(own)}>
                      <Pencil className="w-4 h-4" />
                    </Button>
                  ) : null;
                })()}
                <Button
                  size="sm"
                  disabled={!pick[u.id] || busy !== null}
                  onClick={() => save(pick[u.id], u.id)}
                  className="gap-1.5"
                >
                  <Link2 className="w-4 h-4" />
                  קשר
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading">שמות ותפקידים קיימים</CardTitle>
          <p className="text-xs text-muted-foreground">
            משתמשים חדשים נוצרים ב-Shan-AI (⚙️ מערכת ← 👥 משתמשים). כאן רק מקשרים, ומגדירים תפקיד ופרויקטים במערכת
            הלקחים (✏️).
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.profiles.map((p) => {
            const user = users.find((u) => u.id === p.id);
            return (
              <div key={p.id} className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 p-4 rounded-xl bg-muted/50">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                    <span className="text-sm font-bold text-primary">{p.name.trim()[0]}</span>
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{p.name.trim()}</p>
                    {user?.role === "project_manager" && user.assignedProjects.length > 0 && (
                      <p className="text-xs text-muted-foreground mt-0.5 truncate">
                        פרויקטים: {user.assignedProjects.map((pid) => projects.find((pr) => pr.id === pid)?.name).filter(Boolean).join(", ")}
                      </p>
                    )}
                    {user?.role === "referent" && (user.assignedStageIndexes?.length ?? 0) > 0 && (
                      <p className="text-xs text-muted-foreground mt-0.5 truncate">
                        שלבים: {user.assignedStageIndexes!.map((si) => PROJECT_STAGES[si]).filter(Boolean).join(", ")}
                      </p>
                    )}
                  </div>
                  <Badge variant="outline" className={roleBadgeColors[p.role]}>
                    {roleLabels[p.role] ?? p.role}
                  </Badge>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground shrink-0">משתמש Shan-AI:</span>
                  <SearchableSelect
                    className="w-64"
                    value={p.shan_user_id ? String(p.shan_user_id) : ""}
                    onValueChange={(v) => {
                      const next = v ? Number(v) : null;
                      if (next !== p.shan_user_id) save(p.id, next);
                    }}
                    disabled={busy !== null}
                    searchable
                    options={userOptions}
                  />
                  {p.shan_user_id !== null && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      title="נתק"
                      disabled={busy !== null}
                      onClick={() => save(p.id, null)}
                    >
                      {busy === p.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Link2Off className="w-4 h-4" />}
                    </Button>
                  )}
                  {user && (
                    <Button variant="ghost" size="icon" className="h-8 w-8" title="תפקיד ופרויקטים" onClick={() => onEdit(user)}>
                      <Pencil className="w-4 h-4" />
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
};

export default UserLinksPanel;
