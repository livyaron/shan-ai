import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Lightbulb, Loader2, Search } from "lucide-react";
import { useUser } from "@/context/UserContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

// The module's own login (PLAN-lessons-mobile.md M0). An installed app must not
// leave /lessons/ to sign in, so this page talks to Shan-AI's JSON login routes:
// the same user list and the same password check as Shan-AI's /login page.

interface LoginUser {
  id: number;
  name: string;
  title: string;
}

// Shared with Shan-AI's login page: the user id only — never a password.
const REMEMBERED_KEY = "shan.remembered_user_id";
const NORMAL_DAYS = 7;
const MAX_LISTED = 8;

const store = {
  get: (): string | null => {
    try { return localStorage.getItem(REMEMBERED_KEY); } catch { return null; }
  },
  set: (v: string) => {
    try { localStorage.setItem(REMEMBERED_KEY, v); } catch { /* private mode */ }
  },
  clear: () => {
    try { localStorage.removeItem(REMEMBERED_KEY); } catch { /* private mode */ }
  },
};

const Login = () => {
  const navigate = useNavigate();
  const { isLoggedIn, isLoading } = useUser();
  const [users, setUsers] = useState<LoginUser[] | null>(null);
  const [rememberDays, setRememberDays] = useState(NORMAL_DAYS);
  const [loadError, setLoadError] = useState(false);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<LoginUser | null>(null);
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!isLoading && isLoggedIn) navigate("/", { replace: true });
  }, [isLoggedIn, isLoading, navigate]);

  useEffect(() => {
    let alive = true;
    fetch("/login/users", { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(`/login/users ${r.status}`);
        return r.json();
      })
      .then((body: { users: LoginUser[]; remember_days: number }) => {
        if (!alive) return;
        setUsers(body.users);
        setRememberDays(body.remember_days);
        const saved = store.get();
        const known = body.users.find((u) => String(u.id) === saved);
        if (known) setPicked(known);
        else if (saved) store.clear(); // the user no longer exists
      })
      .catch(() => alive && setLoadError(true));
    return () => {
      alive = false;
    };
  }, []);

  const matches = useMemo(() => {
    const q = query.trim();
    if (!users || !q) return [];
    return users.filter((u) => u.name.includes(q) || u.title.includes(q)).slice(0, MAX_LISTED);
  }, [users, query]);

  const switchUser = () => {
    setPicked(null);
    setPassword("");
    setError(null);
    store.clear();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!picked || !password || sending) return;
    setSending(true);
    setError(null);
    try {
      const r = await fetch("/login/app", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: picked.id, password, remember: remember && rememberDays > NORMAL_DAYS }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.ok) {
        setError(body.error || "הכניסה נכשלה. נסה שוב.");
        return;
      }
      store.set(String(picked.id));
      // A full load, so every screen starts from the new session.
      window.location.replace("/lessons/");
    } catch {
      setError("אין חיבור לשרת. בדוק קליטה ונסה שוב.");
    } finally {
      setSending(false);
    }
  };

  if (isLoading || isLoggedIn) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-primary">
        <div className="w-8 h-8 border-4 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div dir="rtl" className="min-h-[100dvh] bg-primary flex items-start sm:items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm bg-card text-card-foreground rounded-2xl shadow-xl p-6 space-y-6">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-accent flex items-center justify-center">
            <Lightbulb className="w-6 h-6 text-accent-foreground" />
          </div>
          <div>
            <h1 className="font-heading font-bold text-xl">מערכת לקחים</h1>
            <p className="text-sm text-muted-foreground">כניסה עם משתמש Shan-AI</p>
          </div>
        </div>

        {loadError && (
          <p role="alert" className="text-sm text-destructive">
            לא ניתן לטעון את רשימת המשתמשים. בדוק קליטה ורענן.
          </p>
        )}
        {!users && !loadError && <Loader2 className="w-6 h-6 animate-spin mx-auto text-muted-foreground" />}

        {users && (
          <form onSubmit={submit} className="space-y-5">
            {picked ? (
              <div className="flex items-center justify-between gap-2 rounded-xl bg-muted px-4 py-3">
                <div className="min-w-0">
                  <p className="font-medium truncate">{picked.name}</p>
                  {picked.title && <p className="text-xs text-muted-foreground truncate">{picked.title}</p>}
                </div>
                <Button type="button" variant="link" className="shrink-0 h-auto p-0" onClick={switchUser}>
                  החלפת משתמש
                </Button>
              </div>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="login-search">מי נכנס?</Label>
                <div className="relative">
                  <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="login-search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="הקלד שם..."
                    autoComplete="off"
                    enterKeyHint="search"
                    className="h-12 pr-9 text-base"
                  />
                </div>
                <ul className="space-y-1">
                  {matches.map((u) => (
                    <li key={u.id}>
                      <button
                        type="button"
                        onClick={() => { setPicked(u); setQuery(""); }}
                        className="w-full text-right rounded-lg px-4 py-3 min-h-12 hover:bg-muted active:bg-muted border border-border"
                      >
                        <span className="font-medium">{u.name}</span>
                        {u.title && <span className="text-xs text-muted-foreground"> — {u.title}</span>}
                      </button>
                    </li>
                  ))}
                  {query.trim() && matches.length === 0 && (
                    <li className="text-sm text-muted-foreground px-1">לא נמצא משתמש בשם הזה.</li>
                  )}
                </ul>
              </div>
            )}

            {picked && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="login-password">סיסמה</Label>
                  <Input
                    id="login-password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    enterKeyHint="go"
                    autoFocus
                    required
                    className="h-12 text-base"
                  />
                </div>
                {rememberDays > NORMAL_DAYS && (
                  <div className="flex items-center gap-3">
                    <Checkbox id="login-remember" checked={remember} onCheckedChange={(v) => setRemember(v === true)} className="h-5 w-5" />
                    <Label htmlFor="login-remember" className="font-normal">
                      השאר אותי מחובר {rememberDays} ימים
                    </Label>
                  </div>
                )}
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                <Button type="submit" className="w-full h-12 text-base" disabled={!password || sending}>
                  {sending ? <Loader2 className="w-5 h-5 animate-spin" /> : "כניסה"}
                </Button>
              </>
            )}
          </form>
        )}
      </div>
    </div>
  );
};

export default Login;
