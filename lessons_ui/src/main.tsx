import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import ErrorBoundary from "./components/ErrorBoundary.tsx";
import "./index.css";

// A deploy must reach an open tab. The service worker answers navigations
// from its precache, so the first load after a deploy still runs the OLD app
// while the new worker installs (skipWaiting + clientsClaim). When the new
// worker takes over a page that already had one, reload once onto the new
// build. A first-ever install (no previous controller) never reloads.
if ("serviceWorker" in navigator) {
  const hadController = !!navigator.serviceWorker.controller;
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadController || reloading) return;
    reloading = true;
    window.location.reload();
  });
}

createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);
