import { defineConfig } from "vite";

export default defineConfig({
  build: {
    assetsInlineLimit: 0,
    emptyOutDir: true,
    outDir: "src/webchat/static/model3d-assets",
    lib: {
      entry: "src/webchat/model3d_app/main.js",
      name: "CreativeClawModel3D",
      formats: ["iife"],
      fileName: () => "creative-claw-model3d.js",
    },
    rollupOptions: {
      output: {
        entryFileNames: "creative-claw-model3d.js",
      },
    },
  },
});

