import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type * as Monaco from "monaco-editor";
import { changeMarkersFromRows, languageFromPath, reconstructSides } from "../diffText";
import { loadMonaco, type MonacoApi } from "../monacoLoader";
import type { DiffView, ReviewFileDiff } from "../types";

const THEME_LIGHT = "code-harness-volt-light";
const THEME_DARK = "code-harness-volt-dark";

let themesRegistered = false;

function registerThemes(monaco: MonacoApi): void {
  if (themesRegistered) return;
  themesRegistered = true;

  monaco.editor.defineTheme(THEME_DARK, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "5F6B7A", fontStyle: "italic" },
      { token: "string", foreground: "7AF0FF" },
      { token: "keyword", foreground: "C6FF4A" },
      { token: "number", foreground: "FFB86C" },
      { token: "type", foreground: "9DB4FF" },
    ],
    colors: {
      "editor.background": "#0A0C10",
      "editor.foreground": "#E8EDF5",
      "editorLineNumber.foreground": "#3D4654",
      "editorLineNumber.activeForeground": "#C6FF4A",
      "editor.selectionBackground": "#C6FF4A28",
      "editor.lineHighlightBackground": "#12151C",
      "editorDiff.insertedTextBackground": "#C6FF4A22",
      "editorDiff.removedTextBackground": "#FF4D6D28",
      "diffEditor.insertedTextBackground": "#C6FF4A1A",
      "diffEditor.removedTextBackground": "#FF4D6D22",
      "diffEditor.insertedLineBackground": "#C6FF4A12",
      "diffEditor.removedLineBackground": "#FF4D6D14",
      "scrollbarSlider.background": "#C6FF4A22",
      "scrollbarSlider.hoverBackground": "#C6FF4A44",
      "editorGutter.background": "#07080C",
      "editorWidget.background": "#10131A",
      "editorOverviewRuler.border": "#00000000",
    },
  });

  monaco.editor.defineTheme(THEME_LIGHT, {
    base: "vs",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6B7280", fontStyle: "italic" },
      { token: "string", foreground: "0B7A8A" },
      { token: "keyword", foreground: "3D6B00" },
      { token: "number", foreground: "9A4D00" },
      { token: "type", foreground: "2F4BB2" },
    ],
    colors: {
      "editor.background": "#F4F6F2",
      "editor.foreground": "#14181F",
      "editorLineNumber.foreground": "#9AA3AE",
      "editorLineNumber.activeForeground": "#3D6B00",
      "editor.selectionBackground": "#C6FF4A55",
      "editor.lineHighlightBackground": "#EAEFE3",
      "editorDiff.insertedTextBackground": "#8BC34A33",
      "editorDiff.removedTextBackground": "#FF4D6D33",
      "diffEditor.insertedTextBackground": "#8BC34A22",
      "diffEditor.removedTextBackground": "#FF4D6D22",
      "diffEditor.insertedLineBackground": "#8BC34A14",
      "diffEditor.removedLineBackground": "#FF4D6D14",
      "scrollbarSlider.background": "#3D6B0022",
      "editorGutter.background": "#ECEFE8",
      "editorOverviewRuler.border": "#00000000",
    },
  });
}

export function MonacoDiffPane({
  file,
  view,
  theme,
  activeChange,
  loading = false,
  onNavigateRequest,
}: {
  file: ReviewFileDiff | null;
  view: DiffView;
  theme: "light" | "dark";
  activeChange: number;
  loading?: boolean;
  onNavigateRequest?: (index: number) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const editorRef = useRef<Monaco.editor.IStandaloneDiffEditor | null>(null);
  const modelsRef = useRef<{
    original: Monaco.editor.ITextModel;
    modified: Monaco.editor.ITextModel;
  } | null>(null);
  const lineChangesRef = useRef<Monaco.editor.ILineChange[]>([]);
  const modelSequenceRef = useRef(0);
  const [monacoApi, setMonacoApi] = useState<MonacoApi | null>(null);
  const [engineError, setEngineError] = useState<string | null>(null);

  const markers = useMemo(
    () => (file && !file.binary ? changeMarkersFromRows(file.rows) : []),
    [file],
  );
  const engineLoading = !monacoApi && !engineError;
  const hasTextDiff = Boolean(monacoApi && file && !file.binary && file.rows.length);

  const revealChange = useCallback(
    (index: number) => {
      const editor = editorRef.current;
      if (!editor) return;
      const changes = lineChangesRef.current;
      const modified = editor.getModifiedEditor();
      if (changes.length) {
        const change = changes[((index % changes.length) + changes.length) % changes.length];
        const line = change.modifiedStartLineNumber || change.originalStartLineNumber || 1;
        modified.revealLineInCenter(Math.max(1, line));
        modified.setPosition({ lineNumber: Math.max(1, line), column: 1 });
        return;
      }
      if (markers.length) {
        const line = markers[((index % markers.length) + markers.length) % markers.length];
        modified.revealLineInCenter(line);
        modified.setPosition({ lineNumber: line, column: 1 });
      }
    },
    [markers],
  );

  useEffect(() => {
    let cancelled = false;
    loadMonaco()
      .then((api) => {
        if (!cancelled) setMonacoApi(api);
      })
      .catch(() => {
        if (!cancelled) setEngineError("Não foi possível inicializar o Monaco.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!monacoApi) return;
    registerThemes(monacoApi);
    const host = hostRef.current;
    if (!host) return;

    const editor = monacoApi.editor.createDiffEditor(host, {
      automaticLayout: true,
      readOnly: true,
      originalEditable: false,
      renderSideBySide: view === "split",
      useInlineViewWhenSpaceIsLimited: true,
      renderSideBySideInlineBreakpoint: 880,
      renderIndicators: true,
      renderMarginRevertIcon: false,
      renderGutterMenu: false,
      ignoreTrimWhitespace: false,
      enableSplitViewResizing: true,
      renderOverviewRuler: false,
      scrollBeyondLastLine: false,
      smoothScrolling: false,
      cursorBlinking: "solid",
      cursorSmoothCaretAnimation: "off",
      minimap: { enabled: false },
      glyphMargin: false,
      folding: false,
      stickyScroll: { enabled: false },
      codeLens: false,
      links: false,
      occurrencesHighlight: "off",
      selectionHighlight: false,
      maxComputationTime: 3000,
      maxFileSize: 24,
      diffAlgorithm: "advanced",
      hideUnchangedRegions: {
        enabled: true,
        revealLineCount: 14,
        minimumLineCount: 8,
        contextLineCount: 4,
      },
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      fontSize: 13,
      lineHeight: 22,
      padding: { top: 12, bottom: 18 },
      scrollbar: {
        verticalScrollbarSize: 10,
        horizontalScrollbarSize: 10,
        alwaysConsumeMouseWheel: false,
      },
      diffWordWrap: "off",
      theme: theme === "dark" ? THEME_DARK : THEME_LIGHT,
    });

    editorRef.current = editor;
    const diffSubscription = editor.onDidUpdateDiff(() => {
      lineChangesRef.current = editor.getLineChanges() ?? [];
    });

    return () => {
      diffSubscription.dispose();
      editor.setModel(null);
      modelsRef.current?.original.dispose();
      modelsRef.current?.modified.dispose();
      modelsRef.current = null;
      editor.dispose();
      editorRef.current = null;
    };
  }, [monacoApi]);

  useEffect(() => {
    editorRef.current?.updateOptions({
      renderSideBySide: view === "split",
      compactMode: view === "unified",
      experimental: { useTrueInlineView: view === "unified" },
    });
  }, [view]);

  useEffect(() => {
    monacoApi?.editor.setTheme(theme === "dark" ? THEME_DARK : THEME_LIGHT);
  }, [monacoApi, theme]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !monacoApi) return;

    editor.setModel(null);
    modelsRef.current?.original.dispose();
    modelsRef.current?.modified.dispose();
    modelsRef.current = null;
    lineChangesRef.current = [];

    if (!file || file.binary || !file.rows.length) return;

    const language = languageFromPath(file.path);
    const { original, modified } = reconstructSides(file.rows);
    const sequence = ++modelSequenceRef.current;
    const safePath = file.path.replace(/[^a-zA-Z0-9._/-]/g, "_");
    const originalModel = monacoApi.editor.createModel(
      original,
      language,
      monacoApi.Uri.parse(`inmemory://review/${sequence}/original/${safePath}`),
    );
    const modifiedModel = monacoApi.editor.createModel(
      modified,
      language,
      monacoApi.Uri.parse(`inmemory://review/${sequence}/modified/${safePath}`),
    );
    modelsRef.current = { original: originalModel, modified: modifiedModel };
    editor.setModel({ original: originalModel, modified: modifiedModel });

    window.setTimeout(() => {
      if (editorRef.current !== editor) return;
      lineChangesRef.current = editor.getLineChanges() ?? [];
    }, 50);
  }, [file, monacoApi]);

  useEffect(() => {
    if (activeChange < 0 || !hasTextDiff) return;
    revealChange(activeChange);
  }, [activeChange, hasTextDiff, revealChange]);

  const railCount = Math.max(markers.length, 1);

  return (
    <div className={`diff-stage${loading || engineLoading ? " is-loading" : ""}`}>
      <div className="signal-rail" aria-hidden="true">
        {markers.map((line, index) => (
          <button
            key={`${line}-${index}`}
            type="button"
            className={`signal-tick${activeChange === index ? " active" : ""}`}
            style={{ top: `${((index + 0.5) / railCount) * 100}%` }}
            title={`Alteração ${index + 1}`}
            onClick={() => {
              onNavigateRequest?.(index);
              revealChange(index);
            }}
          />
        ))}
      </div>

      <div className={`monaco-host${hasTextDiff ? "" : " is-hidden"}`} ref={hostRef} />

      {!hasTextDiff && (
        <div className="diff-state-overlay">
          {engineError ? (
            <>
              <strong>Monaco indisponível</strong>
              <p>{engineError}</p>
            </>
          ) : engineLoading ? (
            <>
              <div className="volt-pulse" />
              <span>Inicializando o motor do diff…</span>
            </>
          ) : loading || !file ? (
            <>
              <div className="volt-pulse" />
              <span>Sintonizando o próximo impacto…</span>
            </>
          ) : file.binary ? (
            <>
              <strong>Arquivo binário</strong>
              <p>O Monaco compara apenas conteúdo textual neste review.</p>
            </>
          ) : (
            <>
              <strong>Sem diferenças</strong>
              <p>Os snapshots são idênticos linha a linha.</p>
            </>
          )}
        </div>
      )}

      {hasTextDiff && <div className="scan-beam" aria-hidden="true" />}
      {loading && hasTextDiff && <div className="diff-loading-badge">Carregando próximo arquivo</div>}
      {file && (
        <div className="stage-hud">
          <span className="hud-chip">{languageFromPath(file.path)}</span>
          <span className="hud-chip focus">FOCUS MAP</span>
          <span className="hud-chip live">MONACO</span>
        </div>
      )}
    </div>
  );
}

export function countChanges(file: ReviewFileDiff | null, _view?: DiffView): number {
  if (!file || file.binary) return 0;
  return changeMarkersFromRows(file.rows).length;
}

/** @deprecated alias kept for older imports */
export const DiffViewPane = MonacoDiffPane;
