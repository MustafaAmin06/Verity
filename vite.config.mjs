import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  publicDir: false,
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
  build: {
    outDir: path.resolve(path.dirname(new URL(import.meta.url).pathname), "verity-extension/generated"),
    emptyOutDir: true,
    cssCodeSplit: false,
    sourcemap: false,
    lib: {
      entry: path.resolve(path.dirname(new URL(import.meta.url).pathname), "src/inline-ui/boot.jsx"),
      formats: ["iife"],
      name: "VerityInlineUiBundle",
      fileName: () => "inline-ui.js",
    },
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith(".css")) {
            return "inline-ui.css";
          }
          return "[name][extname]";
        },
      },
    },
  },
});
