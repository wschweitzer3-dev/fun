import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const uiRoot = path.resolve(__dirname, "src/invisalign_growth_copilot/ui");

export default defineConfig({
  root: uiRoot,
  plugins: [react()],
  resolve: {
    alias: {
      "@": uiRoot
    }
  },
  server: {
    port: 5173
  },
  build: {
    outDir: path.resolve(__dirname, "dist/ui"),
    emptyOutDir: true
  }
});
