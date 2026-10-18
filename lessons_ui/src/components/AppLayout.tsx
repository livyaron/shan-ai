import { useState, useEffect } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { LogOut, Menu, Smartphone } from "lucide-react";
import AppSidebar from "./AppSidebar";
import UserSwitcher from "./UserSwitcher";
import DarkModeToggle from "./DarkModeToggle";
import { Button } from "./ui/button";
import { useUser } from "@/context/UserContext";
import FeedbackButton from "./FeedbackButton";
import ShanNavbar from "./ShanNavbar";
import { isStandalone, opensPhoneHome, setPrefersDesktop } from "@/lib/standalone";

// Height of Shan-AI's top bar (set by ShanNavbar); 0 until it loads.
const NAV_H = "var(--shan-nav-h, 0px)";

const AppLayout = () => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { isLoggedIn, isLoading, currentUser, logout } = useUser();
  // Installed app (PLAN-lessons-mobile.md M0): no Shan-AI top bar — it costs a
  // phone screen two rows and links to pages outside the app. Logout moves here.
  const [standalone] = useState(isStandalone);
  const navigate = useNavigate();
  const { pathname } = useLocation();

  useEffect(() => {
    if (!isLoading && !isLoggedIn) {
      navigate("/login", { replace: true });
    }
  }, [isLoggedIn, isLoading, navigate]);

  // A phone opening the app (start_url = /lessons/) goes to the phone screens NOW,
  // not after the computer frame has loaded everything behind a spinner: the phone
  // frame shows the opening picture while it loads (owner, 2026-10-19).
  if (pathname === "/" && opensPhoneHome()) return <Navigate to="/m" replace />;

  if (isLoading || !isLoggedIn) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-8 h-8 border-4 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background animate-fade-in">
      {!standalone && <ShanNavbar />}
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar - desktop: always visible, mobile: slide-in */}
      <div
        className={`
        fixed right-0 z-50 transition-transform duration-300
        md:translate-x-0
        ${sidebarOpen ? "translate-x-0" : "translate-x-full md:translate-x-0"}
      `}
        style={{ top: NAV_H, height: `calc(100vh - ${NAV_H})` }}
      >
        <AppSidebar onNavigate={() => setSidebarOpen(false)} />
      </div>

      <main className="md:mr-64 min-h-screen">
        {/* Top bar */}
        <div style={{ top: NAV_H }} className="sticky z-30 bg-background/80 backdrop-blur-sm border-b border-border px-4 md:px-8 py-3 flex items-center justify-between">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="w-5 h-5" />
          </Button>
          <div className="hidden md:block" />
          <Button variant="ghost" size="sm" className="md:hidden gap-1" onClick={() => { setPrefersDesktop(false); navigate("/m"); }}>
            <Smartphone className="w-4 h-4" /> תצוגת טלפון
          </Button>
          <div className="flex items-center gap-2">
            <DarkModeToggle />
            {currentUser.role !== "viewer" && <FeedbackButton />}
            <UserSwitcher />
            {standalone && (
              <Button variant="ghost" size="icon" onClick={logout} aria-label="יציאה" title="יציאה">
                <LogOut className="w-5 h-5" />
              </Button>
            )}
          </div>
        </div>
        <div className="p-4 md:p-8 animate-fade-in">
          <Outlet />
        </div>
      </main>
    </div>
  );
};

export default AppLayout;
