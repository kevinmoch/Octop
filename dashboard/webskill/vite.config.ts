import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: [
      {
        find: /^node:module$/,
        replacement: resolve(import.meta.dirname, "src/nodeModuleBoundary.ts"),
      },
    ],
  },
  build: {
    outDir: resolve(import.meta.dirname, "../../src/octop/dashboard/webskill"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
  },
});
