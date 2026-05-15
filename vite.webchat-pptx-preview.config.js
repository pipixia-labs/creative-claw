import { defineConfig } from "vite";

export default defineConfig({
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
  build: {
    assetsInlineLimit: 0,
    emptyOutDir: true,
    outDir: "src/webchat/static/pptx-preview-assets",
    lib: {
      entry: "src/webchat/pptx_preview_app/main.js",
      name: "CreativeClawPptxPreview",
      formats: ["iife"],
      fileName: () => "creative-claw-pptx-preview.js",
    },
    rollupOptions: {
      output: {
        entryFileNames: "creative-claw-pptx-preview.js",
      },
    },
  },
});
