import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { UserProvider, useUser } from "./context/UserContext";
import AppLayout from "./components/AppLayout";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import Lessons from "./pages/Lessons";
import LessonDetail from "./pages/LessonDetail";
import Notifications from "./pages/Notifications";
import Admin from "./pages/Admin";
import Login from "./pages/Login";
import Install from "./pages/Install";
import NotFound from "./pages/NotFound";
import ScrollToTop from "./components/ScrollToTop";

const AdminRoute = () => {
  const { currentUser } = useUser();
  if (currentUser.role !== "admin") return <Navigate to="/" replace />;
  return <Admin />;
};

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <UserProvider>
        <BrowserRouter basename="/lessons">
          <ScrollToTop />
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/install" element={<Install />} />
            <Route element={<AppLayout />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/projects" element={<Projects />} />
              <Route path="/projects/:id" element={<ProjectDetail />} />
              <Route path="/lessons" element={<Lessons />} />
              <Route path="/lessons/:id" element={<LessonDetail />} />
              <Route path="/notifications" element={<Notifications />} />
              <Route path="/admin" element={<AdminRoute />} />
            </Route>
            <Route path="*" element={<NotFound />} />
          </Routes>
        </BrowserRouter>
      </UserProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
