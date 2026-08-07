import editorWorker from "monaco-editor/editor/editor.worker.js?worker";

declare global {
  interface Window {
    MonacoEnvironment?: {
      getWorker: (_: unknown, label: string) => Worker;
    };
  }
}

/** Diff review only needs the core editor worker (no language services). */
export function setupMonacoEnvironment(): void {
  self.MonacoEnvironment = {
    getWorker() {
      return new editorWorker();
    },
  };
}
