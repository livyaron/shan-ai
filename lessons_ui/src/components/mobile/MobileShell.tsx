import { useEffect } from "react";
import { Link, Outlet, useNavigate } from "react-router-dom";
import { useUser } from "@/context/UserContext";

/**
 * Full-screen frame for the phone screens under /m (PLAN-lessons-mobile.md):
 * no sidebar, no Shan-AI navbar — a wizard needs the whole phone.
 */
const MobileShell = () => {
  const { isLoggedIn, isLoading, hasPermission } = useUser();
  const navigate = useNavigate();

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

  if (!hasPermission("add_lessons")) {
    return (
      <div dir="rtl" className="min-h-[100dvh] flex flex-col items-center justify-center gap-4 p-6 text-center bg-background">
        <p className="text-lg font-medium">אין לך הרשאה להזין לקחים.</p>
        <Link to="/lessons" className="text-primary underline">חזרה ללקחים</Link>
      </div>
    );
  }

  return (
    <div dir="rtl" className="min-h-[100dvh] bg-background text-foreground">
      <Outlet />
    </div>
  );
};

export default MobileShell;
