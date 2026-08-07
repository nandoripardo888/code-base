import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/syne/600.css";
import "@fontsource/syne/700.css";
import "@fontsource/syne/800.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import "monaco-editor/dev/vs/editor/editor.main.css";
import { setupMonacoEnvironment } from "./monacoEnv";
import { App } from "./App";
import "./styles.css";

setupMonacoEnvironment();

const root = document.getElementById("root");
if (!root) {
  throw new Error("Elemento #root não encontrado.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
