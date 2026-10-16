import { useEffect } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { BookOpen, ClipboardCheck, FileEdit, Home, Plus } from "lucide-react";
import { useUser } from "@/context/UserContext";
import { cn } from "@/lib/utils";
import { showsTabBar } from "@/lib/standalone";
import { useMyTasks } from "@/hooks/useMyTasks";

/**
 * Full-screen frame for the phone screens under /m (PLAN-lessons-mobile.md):
 * no sidebar, no Shan-AI navbar. A bottom tab bar on the browsing screens;
 * the wizard keeps the whole phone for itself.
 */

const TABS = [
  { to: "/m", label: "בית", icon: Home, end: true },
  { to: "/m/lessons", label: "לקחים", icon: BookOpen, end: false },
  { to: "/m/tasks", label: "משימות", icon: ClipboardCheck, end: true },
  { to: "/m/drafts", label: "טיוטות", icon: FileEdit, end: true },
];

const MobileShell = () => {
  const { isLoggedIn, isLoading, hasPermission } = useUser();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const canAdd = hasPermission("add_lessons");
  const isWizard = !showsTabBar(pathname);
  const taskCount = useMyTasks().length;

  useEffect(() => {
    if (!isLoading && !isLoggedIn) navigate("/login", { replace: true });
  }, [isLoggedIn, isLoading, navigate]);

  if (isLoading || !isLoggedIn) {
    return (
      <div className="min-h-[100dvh] flex items-center justify-center bg-background">
        <div className="w-8 h-8 border-4 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!canAdd && (isWizard || pathname.startsWith("/m/drafts"))) {
    return (
      <div dir="rtl" className="min-h-[100dvh] flex flex-col items-center justify-center gap-4 p-6 text-center bg-background">
        <p className="text-lg font-medium">אין לך הרשאה להזין לקחים.</p>
        <Link to="/m/lessons" className="text-primary underline">לרשימת הלקחים</Link>
      </div>
    );
  }

  return (
    <div dir="rtl" className="min-h-[100dvh] bg-background text-foreground">
      <Outlet />
      {!isWizard && (
        <nav
          aria-label="ניווט"
          className="fixed bottom-0 inset-x-0 z-30 bg-background/95 backdrop-blur border-t border-border pb-[env(safe-area-inset-bottom)]"
        >
          <div className="grid grid-cols-5 h-16">
            {TABS.slice(0, 2).map((t) => <Tab key={t.to} {...t} />)}
            {canAdd ? (
              <Link to="/m/new" aria-label="לקח חדש" className="flex items-center justify-center">
                <span className="w-12 h-12 -mt-5 rounded-full bg-accent text-accent-foreground shadow-lg flex items-center justify-center">
                  <Plus className="w-6 h-6" />
                </span>
              </Link>
            ) : <span />}
            <Tab {...TABS[2]} badge={taskCount} />
            {canAdd ? <Tab {...TABS[3]} /> : <span />}
          </div>
        </nav>
      )}
    </div>
  );
};

const Tab = ({ to, label, icon: Icon, end, badge }: (typeof TABS)[number] & { badge?: number }) => (
  <NavLink
    to={to}
    end={end}
    className={({ isActive }) =>
      cn("flex flex-col items-center justify-center gap-0.5 text-xs", isActive ? "text-primary font-medium" : "text-muted-foreground")
    }
  >
    <span className="relative">
      <Icon className="w-5 h-5" />
      {!!badge && (
        <span className="absolute -top-1.5 -left-2 min-w-4 h-4 px-1 rounded-full bg-destructive text-[10px] leading-4 text-destructive-foreground text-center">{badge}</span>
      )}
    </span>
    {label}
  </NavLink>
);

export default MobileShell;
