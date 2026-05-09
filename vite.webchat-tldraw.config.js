import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
  plugins: [react()],
  build: {
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    emptyOutDir: true,
    outDir: "src/webchat/static/tldraw-assets",
    lib: {
      entry: "src/webchat/tldraw_app/main.jsx",
      name: "CreativeClawTldrawBundle",
      formats: ["iife"],
      fileName: () => "creative-claw-tldraw.js",
    },
    rollupOptions: {
      output: {
        assetFileNames: (assetInfo) => {
          if (assetInfo.name?.endsWith(".css")) {
            return "creative-claw-tldraw.css";
          }
          return "assets/[name][extname]";
        },
        entryFileNames: "creative-claw-tldraw.js",
      },
    },
  },
});
