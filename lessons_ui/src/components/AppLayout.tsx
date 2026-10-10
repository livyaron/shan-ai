import { useState, useEffect } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { Menu } from "lucide-react";
import AppSidebar from "./AppSidebar";
import UserSwitcher from "./UserSwitcher";
import DarkModeToggle from "./DarkModeToggle";
import { Button } from "./ui/button";
import { useUser } from "@/context/UserContext";
import FeedbackButton from "./FeedbackButton";

const AppLayout = () => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { isLoggedIn, isLoading, currentUser } = useUser();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isLoading && !isLoggedIn) {
      navigate("/login", { replace: true });
    }
  }, [isLoggedIn, isLoading, navigate]);

  if (isLoading || !isLoggedIn) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-8 h-8 border-4 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background animate-fade-in">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar - desktop: always visible, mobile: slide-in */}
      <div className={`
        fixed top-0 right-0 h-screen z-50 transition-transform duration-300
        md:translate-x-0
        ${sidebarOpen ? "translate-x-0" : "translate-x-full md:translate-x-0"}
      `}>
        <AppSidebar onNavigate={() => setSidebarOpen(false)} />
      </div>

      <main className="md:mr-64 min-h-screen">
        {/* Top bar */}
        <div className="sticky top-0 z-30 bg-background/80 backdrop-blur-sm border-b border-border px-4 md:px-8 py-3 flex items-center justify-between">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="w-5 h-5" />
          </Button>
          <div className="hidden md:block" />
          <div className="flex items-center gap-2">
            <DarkModeToggle />
            {currentUser.role !== "viewer" && <FeedbackButton />}
            <UserSwitcher />
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
