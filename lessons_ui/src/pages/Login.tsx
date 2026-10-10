import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useUser } from "@/context/UserContext";
import { SHAN_LOGIN_URL } from "@/integrations/supabase/client";

// Login is Shan-AI's (PLAN-lessons-module.md decision 1). This route only
// sends the browser on: home when the session is valid, else to /login.
const Login = () => {
  const navigate = useNavigate();
  const { isLoggedIn, isLoading } = useUser();

  useEffect(() => {
    if (isLoading) return;
    if (isLoggedIn) navigate("/", { replace: true });
    else window.location.href = SHAN_LOGIN_URL;
  }, [isLoggedIn, isLoading, navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-primary">
      <div className="w-8 h-8 border-4 border-accent border-t-transparent rounded-full animate-spin" />
    </div>
  );
};

export default Login;
