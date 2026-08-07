import type { SideBySideRow } from "./types";

const LANGUAGE_BY_EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  java: "java",
  kt: "kotlin",
  go: "go",
  rs: "rust",
  rb: "ruby",
  php: "php",
  cs: "csharp",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  h: "cpp",
  hpp: "cpp",
  c: "c",
  sql: "sql",
  pls: "sql",
  pks: "sql",
  pkb: "sql",
  json: "json",
  jsonc: "json",
  md: "markdown",
  markdown: "markdown",
  html: "html",
  htm: "html",
  xml: "xml",
  css: "css",
  scss: "scss",
  less: "less",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  ps1: "powershell",
  bat: "bat",
  cmd: "bat",
  dockerfile: "dockerfile",
  graphql: "graphql",
  gql: "graphql",
};

export function languageFromPath(path: string): string {
  const base = path.split(/[\\/]/).pop() ?? path;
  if (/^dockerfile$/i.test(base)) return "dockerfile";
  const ext = base.includes(".") ? base.split(".").pop()!.toLowerCase() : "";
  return LANGUAGE_BY_EXT[ext] ?? "plaintext";
}

/** Rebuild full file sides from aligned diff rows (expects full context). */
export function reconstructSides(rows: SideBySideRow[]): {
  original: string;
  modified: string;
} {
  const original: string[] = [];
  const modified: string[] = [];
  for (const row of rows) {
    if (row.kind === "collapsed") continue;
    if (row.old_text !== null) original.push(row.old_text);
    if (row.new_text !== null) modified.push(row.new_text);
  }
  return {
    original: original.join("\n"),
    modified: modified.join("\n"),
  };
}

export function changeMarkersFromRows(rows: SideBySideRow[]): number[] {
  const markers: number[] = [];
  let modifiedLine = 0;
  let insideChange = false;

  for (const row of rows) {
    if (row.kind === "collapsed") {
      insideChange = false;
      continue;
    }

    if (row.new_text !== null) modifiedLine += 1;

    if (row.kind === "context") {
      insideChange = false;
      continue;
    }

    if (!insideChange) {
      // One marker per contiguous hunk keeps navigation and the signal rail lightweight.
      markers.push(Math.max(1, modifiedLine));
      insideChange = true;
    }
  }

  return markers;
}
