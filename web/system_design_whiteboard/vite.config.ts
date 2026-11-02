import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cpSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));
const outputDirectory = resolve(projectRoot, "../../spikes/system_design_whiteboard_probe/dist");
const excalidrawAssets = resolve(projectRoot, "node_modules/@excalidraw/excalidraw/dist/excalidraw-assets");

export default defineConfig({
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/excalidraw-assets": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: outputDirectory,
    emptyOutDir: true,
  },
  plugins: [
    react(),
    {
      name: "self-host-excalidraw-assets",
      apply: "build",
      closeBundle() {
        const destination = resolve(outputDirectory, "excalidraw-assets");
        mkdirSync(destination, { recursive: true });
        cpSync(excalidrawAssets, destination, { recursive: true });
      },
    },
  ],
});
