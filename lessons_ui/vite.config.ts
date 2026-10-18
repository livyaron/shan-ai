import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { VitePWA } from "vite-plugin-pwa";

// Served by Shan-AI under /lessons/ (PLAN-lessons-module.md §2.3).
export default defineConfig(() => ({
  base: "/lessons/",
  server: {
    host: "::",
    port: 8080,
    hmr: {
      overlay: false,
    },
  },
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.ico"],
      workbox: {
        navigateFallback: "/lessons/index.html",
        // API calls are never answered from the cache.
        navigateFallbackDenylist: [/^\/lessons\/api\//],
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2,webp}"],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
      manifest: {
        name: "מערכת לקחים - ניהול חכם",
        short_name: "לקחים",
        description: "מערכת חכמה לניהול לקחים בפרויקטי בנייה ותשתיות",
        theme_color: "#1a2744",
        // = --background: Android's system splash hands over to the opening screen without a flash.
        background_color: "#F5F6F8",
        display: "standalone",
        dir: "rtl",
        lang: "he",
        start_url: "/lessons/",
        scope: "/lessons/",
        // Long-press the icon (Android): straight into the wizard.
        shortcuts: [
          { name: "לקח חדש", short_name: "לקח חדש", url: "/lessons/m/new", icons: [{ src: "pwa-192x192.png", sizes: "192x192" }] },
          { name: "הטיוטות שלי", short_name: "טיוטות", url: "/lessons/m/drafts", icons: [{ src: "pwa-192x192.png", sizes: "192x192" }] },
        ],
        icons: [
          {
            src: "pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            // Same art with a wider margin: Android cuts adaptive icons to a circle/squircle.
            src: "pwa-maskable-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
    }),
  ].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
