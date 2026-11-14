/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  test: { environment: "node" },
  build: { chunkSizeWarningLimit: 1200 }, // maplibre-gl alone is ~1 MB
});
