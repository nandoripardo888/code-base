import type * as Monaco from "monaco-editor";

export type MonacoApi = typeof Monaco;

let monacoPromise: Promise<MonacoApi> | null = null;

/** Load Monaco once, outside the initial application bundle. */
export function loadMonaco(): Promise<MonacoApi> {
  if (!monacoPromise) {
    monacoPromise = import("monaco-editor");
  }
  return monacoPromise;
}
