import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  FolderKanban,
  BookOpen,
  Bell,
  Settings,
  LogOut,
  Lightbulb,
} from "lucide-react";
import { useUser, roleLabels } from "@/context/UserContext";
import { Badge } from "@/components/ui/badge";
import { useActionRequired } from "@/hooks/useActionRequired";

const allNavItems = [
  { label: "דשבורד", icon: LayoutDashboard, path: "/", roles: ["admin", "project_manager", "team_member", "referent", "viewer"] },
  { label: "פרויקטים", icon: FolderKanban, path: "/projects", roles: ["admin", "project_manager", "team_member", "viewer"] },
  { label: "לקחים", icon: BookOpen, path: "/lessons", roles: ["admin", "project_manager", "team_member", "referent", "viewer"] },
  { label: "התראות", icon: Bell, path: "/notifications", roles: ["admin", "project_manager", "team_member", "referent", "viewer"] },
  { label: "ניהול מערכת", icon: Settings, path: "/admin", roles: ["admin"] },
];

interface AppSidebarProps {
  onNavigate?: () => void;
}

const AppSidebar = ({ onNavigate }: AppSidebarProps) => {
  const location = useLocation();
  const { currentUser, logout, unreadCount, lessons, implementations, projects, referentReviews } = useUser();
  const { lessonsActionCount, projectsActionCount } = useActionRequired(lessons, implementations, projects, currentUser, referentReviews);

  const navItems = allNavItems.filter((item) => item.roles.includes(currentUser.role));

  return (
    <aside className="h-screen w-64 bg-sidebar text-sidebar-foreground flex flex-col">
      {/* Logo */}
      <div className="p-6 flex items-center gap-3 border-b border-sidebar-border">
        <div className="w-10 h-10 rounded-lg bg-sidebar-primary flex items-center justify-center">
          <Lightbulb className="w-5 h-5 text-sidebar-primary-foreground" />
        </div>
        <div>
          <h1 className="font-heading font-bold text-base text-sidebar-accent-foreground">
            מערכת לקחים
          </h1>
          <p className="text-xs text-sidebar-foreground/60">ניהול חכם</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1">
        {navItems.map((item) => {
          const isActive = location.pathname === item.path;
          return (
            <Link
              key={item.path}
              to={item.path}
              onClick={onNavigate}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
                isActive
                  ? "bg-sidebar-primary text-sidebar-primary-foreground shadow-lg"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              }`}
            >
              <item.icon className="w-5 h-5" />
              <span className="flex-1">{item.label}</span>
              {item.path === "/notifications" && unreadCount > 0 && (
                <span className="w-5 h-5 bg-destructive text-destructive-foreground text-[10px] rounded-full flex items-center justify-center font-bold">
                  {unreadCount}
                </span>
              )}
              {item.path === "/lessons" && lessonsActionCount > 0 && (
                <span className="w-5 h-5 bg-warning text-warning-foreground text-[10px] rounded-full flex items-center justify-center font-bold animate-pulse">
                  {lessonsActionCount}
                </span>
              )}
              {item.path === "/projects" && currentUser.role === "project_manager" && (() => {
                const pmProjectCount = projects.filter(
                  (p) => currentUser.assignedProjects.includes(p.id) || p.managerId === currentUser.id
                ).length;
                return pmProjectCount > 0 ? (
                  <span className="w-5 h-5 bg-primary text-primary-foreground text-[10px] rounded-full flex items-center justify-center font-bold">
                    {pmProjectCount}
                  </span>
                ) : null;
              })()}
            </Link>
          );
        })}
      </nav>

      {/* User section */}
      <div className="p-4 border-t border-sidebar-border">
        <div className="flex items-center gap-3 px-4 py-3">
          <div className="w-9 h-9 rounded-full bg-sidebar-accent flex items-center justify-center">
            <span className="text-sm font-bold text-sidebar-accent-foreground">{currentUser.name[0]}</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-sidebar-accent-foreground truncate">{currentUser.name}</p>
            <p className="text-xs text-sidebar-foreground/50">{roleLabels[currentUser.role]}</p>
          </div>
          <button onClick={logout} className="text-sidebar-foreground/50 hover:text-sidebar-primary transition">
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>
  );
};

export default AppSidebar;
