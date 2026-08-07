import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  publicDir: false,
  base: "./",
  resolve: {
    alias: {
      // package exports remap *.css incorrectly; pin the real stylesheet
      "monaco-editor/dev/vs/editor/editor.main.css": path.resolve(
        __dirname,
        "node_modules/monaco-editor/min/vs/editor/editor.main.css",
      ),
    },
  },
  worker: {
    format: "es",
  },
  build: {
    outDir: path.resolve(__dirname, "../src/code_harness/review/static"),
    emptyOutDir: false,
    cssCodeSplit: false,
    assetsInlineLimit: 4096,
    modulePreload: false,
    rollupOptions: {
      input: path.resolve(__dirname, "src/main.tsx"),
      output: {
        format: "es",
        entryFileNames: "review.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: (asset) => {
          if (asset.name && asset.name.endsWith(".css")) return "review.css";
          if (asset.name && /\.(woff2?|ttf|otf)$/i.test(asset.name)) {
            return "fonts/[name][extname]";
          }
          return "assets/[name]-[hash][extname]";
        },
      },
    },
  },
});
