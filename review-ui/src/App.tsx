import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, bootstrap } from "./api";
import { MonacoDiffPane, countChanges } from "./components/DiffView";
import {
  AlertIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  HistoryIcon,
  MoonIcon,
  ReviewIcon,
  SunIcon,
  UndoIcon,
} from "./components/Icons";
import { Sidebar } from "./components/Sidebar";
import type {
  DiffView,
  PatchGroupListItem,
  PatchGroupSummary,
  PatchListItem,
  PatchSummary,
  ReviewFileDiff,
  ReviewFileSummary,
} from "./types";
import {
  formatDate,
  formatSource,
  operationLabel,
  readUrlState,
  writeGroupUrlState,
  writeUrlState,
} from "./utils";

type ToastItem = { id: number; message: string; error?: boolean };
type DialogKind = "patch" | "group" | null;
type SelectionMode = "group" | "file";
type ReviewQueueItem = {
  key: string;
  patch: PatchListItem;
  patchIndex: number;
  file: ReviewFileSummary;
};

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const stored = localStorage.getItem("code-harness-review-theme");
    if (stored === "dark" || stored === "light") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("code-harness-review-theme", theme);
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  };
}

export function App() {
  const offline =
    window.location.protocol === "file:" ||
    !bootstrap.csrfToken ||
    bootstrap.csrfToken.includes("__");

  const { theme, toggle } = useTheme();
  const [groups, setGroups] = useState<PatchGroupListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [groupId, setGroupId] = useState("");
  const [transactionId, setTransactionId] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [groupSummary, setGroupSummary] = useState<PatchGroupSummary | null>(null);
  const [patchSummary, setPatchSummary] = useState<PatchSummary | null>(null);
  const [fileDiff, setFileDiff] = useState<ReviewFileDiff | null>(null);
  const [groupItems, setGroupItems] = useState<ReviewQueueItem[]>([]);
  const [groupSelectedIndex, setGroupSelectedIndex] = useState(0);
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("file");
  const [loadingFile, setLoadingFile] = useState(false);
  const [loadingGroup, setLoadingGroup] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<DiffView>(() =>
    window.matchMedia("(max-width: 900px)").matches ? "unified" : "split",
  );
  const [activeChange, setActiveChange] = useState(-1);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set());
  const [expandedPatches, setExpandedPatches] = useState<Set<string>>(() => new Set());
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [busy, setBusy] = useState(false);
  const [impact, setImpact] = useState(false);
  const toastId = useRef(0);
  const fileCacheRef = useRef(new Map<string, ReviewFileDiff>());
  const filePromiseRef = useRef(new Map<string, Promise<ReviewFileDiff>>());
  const fileRequestRef = useRef(0);

  const toast = useCallback((message: string, isError = false) => {
    const id = ++toastId.current;
    setToasts((items) => [...items, { id, message, error: isError }]);
    window.setTimeout(() => {
      setToasts((items) => items.filter((item) => item.id !== id));
    }, 5200);
  }, []);

  const loadGroups = useCallback(async () => {
    const payload = await api.listGroups(200);
    setGroups(payload.items);
    setTotal(payload.total);
    return payload.items;
  }, []);

  const getReviewFile = useCallback((gId: string, txId: string, index: number) => {
    const key = `${gId}:${txId}:${index}`;
    const cached = fileCacheRef.current.get(key);
    if (cached) {
      fileCacheRef.current.delete(key);
      fileCacheRef.current.set(key, cached);
      return Promise.resolve(cached);
    }

    const pending = filePromiseRef.current.get(key);
    if (pending) return pending;

    const request = api
      .getFile(gId, txId, index, true)
      .then((diff) => {
        fileCacheRef.current.set(key, diff);
        if (fileCacheRef.current.size > 24) {
          const oldestKey = fileCacheRef.current.keys().next().value;
          if (oldestKey) fileCacheRef.current.delete(oldestKey);
        }
        return diff;
      })
      .finally(() => filePromiseRef.current.delete(key));
    filePromiseRef.current.set(key, request);
    return request;
  }, []);

  const loadFile = useCallback(
    async (
      gId: string,
      txId: string,
      index: number,
      targetChange: "first" | "last" | null = null,
    ) => {
      const requestId = ++fileRequestRef.current;
      setLoadingFile(true);
      setError(null);
      setFileDiff(null);
      setActiveChange(-1);
      try {
        const diff = await getReviewFile(gId, txId, index);
        if (requestId !== fileRequestRef.current) return;
        setFileDiff(diff);
        const totalChanges = countChanges(diff);
        if (targetChange === "first") setActiveChange(totalChanges ? 0 : -1);
        if (targetChange === "last") setActiveChange(totalChanges ? totalChanges - 1 : -1);
      } catch (err) {
        if (requestId === fileRequestRef.current) {
          setError(err instanceof Error ? err.message : "Falha ao carregar diff");
        }
      } finally {
        if (requestId === fileRequestRef.current) setLoadingFile(false);
      }
    },
    [getReviewFile],
  );

  const prefetchFile = useCallback(
    (gId: string, txId: string, index: number) => {
      window.setTimeout(() => {
        getReviewFile(gId, txId, index).catch(() => undefined);
      }, 120);
    },
    [getReviewFile],
  );

  const loadGroupOverview = useCallback(
    async (gId: string) => {
      setLoadingGroup(true);
      setSelectionMode("group");
      setError(null);
      setGroupItems([]);
      setFileDiff(null);
      setPatchSummary(null);
      setActiveChange(-1);
      try {
        const group = await api.getGroup(gId);
        const items: ReviewQueueItem[] = group.patches.flatMap((patch, patchIndex) =>
          patch.files.map((file) => ({
            key: `${patch.transaction_id}:${file.index}`,
            patch,
            patchIndex,
            file,
          })),
        );
        const focusIndex = items.reduce((bestIndex, item, index) => {
          const best = items[bestIndex];
          const impact = item.file.additions + item.file.deletions;
          const bestImpact = best ? best.file.additions + best.file.deletions : -1;
          return impact > bestImpact ? index : bestIndex;
        }, 0);

        setGroupSummary(group);
        setGroupId(gId);
        setGroupItems(items);
        setGroupSelectedIndex(focusIndex);
        setExpandedGroups((prev) => new Set(prev).add(gId));
        const focus = items[focusIndex];
        if (focus) {
          setExpandedPatches((prev) => new Set(prev).add(focus.patch.transaction_id));
        }
        writeGroupUrlState(gId);

        if (focus) {
          setTransactionId(focus.patch.transaction_id);
          await loadFile(gId, focus.patch.transaction_id, focus.file.index);
          const previous = items[(focusIndex - 1 + items.length) % items.length];
          const next = items[(focusIndex + 1) % items.length];
          if (previous && previous.key !== focus.key) {
            prefetchFile(gId, previous.patch.transaction_id, previous.file.index);
          }
          if (next && next.key !== focus.key && next.key !== previous?.key) {
            prefetchFile(gId, next.patch.transaction_id, next.file.index);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Falha ao carregar o review completo");
      } finally {
        setLoadingGroup(false);
      }
    },
    [loadFile, prefetchFile],
  );

  const loadSummary = useCallback(
    async (gId: string, txId: string, fileIndex = 0) => {
      const [group, patch] = await Promise.all([api.getGroup(gId), api.getPatch(gId, txId)]);
      setSelectionMode("file");
      setGroupItems([]);
      setGroupSummary(group);
      setPatchSummary(patch);
      setGroupId(gId);
      setTransactionId(txId);
      setExpandedGroups((prev) => new Set(prev).add(gId));
      setExpandedPatches((prev) => new Set(prev).add(txId));
      if (patch.files.length) {
        const selected = Math.min(Math.max(fileIndex, 0), patch.files.length - 1);
        setSelectedIndex(selected);
        writeUrlState(gId, txId, selected);
        await loadFile(gId, txId, selected);
      } else {
        setSelectedIndex(0);
        setFileDiff(null);
        setError("Nenhum arquivo nesta revisão.");
      }
    },
    [loadFile],
  );

  const selectPatch = useCallback(
    async (gId: string, txId: string, fileIndex = 0) => {
      try {
        await loadSummary(gId, txId, fileIndex);
      } catch (err) {
        toast(err instanceof Error ? err.message : "Falha ao abrir patch", true);
      }
    },
    [loadSummary, toast],
  );

  const selectGroup = useCallback(
    (gId: string) => loadGroupOverview(gId),
    [loadGroupOverview],
  );

  useEffect(() => {
    if (offline) return;
    let cancelled = false;
    (async () => {
      try {
        const items = await loadGroups();
        if (cancelled) return;
        if (!items.length) {
          setError(null);
          setGroupSummary(null);
          setPatchSummary(null);
          setFileDiff(null);
          setGroupItems([]);
          return;
        }
        const url = readUrlState();
        let gId = items[0].group_id;
        let txId = items[0].patches.at(-1)!.transaction_id;
        if (url.group && items.some((item) => item.group_id === url.group)) {
          gId = url.group;
          const group = items.find((item) => item.group_id === url.group)!;
          if (url.patch && group.patches.some((p) => p.transaction_id === url.patch)) {
            txId = url.patch;
          } else {
            txId = group.patches.at(-1)!.transaction_id;
          }
        }
        if (url.scope === "group") {
          await loadGroupOverview(gId);
        } else {
          await loadSummary(gId, txId, url.file);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Falha ao iniciar");
      }
    })();
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") {
        loadGroups().catch(() => undefined);
      }
    };
    const timer = window.setInterval(refreshWhenVisible, 60000);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [offline, loadGroupOverview, loadGroups, loadSummary]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    const onChange = (event: MediaQueryListEvent) => {
      if (event.matches) setView("unified");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    setActiveChange(-1);
  }, [selectionMode, view]);

  const activeGroupItem = groupItems[groupSelectedIndex] ?? null;
  const changeCount = countChanges(fileDiff, view);

  const openGroupFile = useCallback(
    (index: number, targetChange: "first" | "last" | null = null) => {
      if (!groupItems.length) return;
      const nextIndex = ((index % groupItems.length) + groupItems.length) % groupItems.length;
      const item = groupItems[nextIndex];
      setGroupSelectedIndex(nextIndex);
      setTransactionId(item.patch.transaction_id);
      writeGroupUrlState(groupId);
      void loadFile(groupId, item.patch.transaction_id, item.file.index, targetChange);

      const previous = groupItems[(nextIndex - 1 + groupItems.length) % groupItems.length];
      const next = groupItems[(nextIndex + 1) % groupItems.length];
      if (previous && previous.key !== item.key) {
        prefetchFile(groupId, previous.patch.transaction_id, previous.file.index);
      }
      if (next && next.key !== item.key && next.key !== previous?.key) {
        prefetchFile(groupId, next.patch.transaction_id, next.file.index);
      }
    },
    [groupId, groupItems, loadFile, prefetchFile],
  );

  const stepGroupFile = useCallback(
    (direction: number) => openGroupFile(groupSelectedIndex + direction),
    [groupSelectedIndex, openGroupFile],
  );

  const navigateChange = useCallback(
    (direction: number) => {
      if (selectionMode === "group") {
        if (!changeCount) {
          openGroupFile(groupSelectedIndex + direction, direction > 0 ? "first" : "last");
          return;
        }
        const current = activeChange < 0 ? (direction > 0 ? -1 : changeCount) : activeChange;
        const next = current + direction;
        if (next < 0 || next >= changeCount) {
          openGroupFile(groupSelectedIndex + direction, direction > 0 ? "first" : "last");
          return;
        }
        setActiveChange(next);
        return;
      }
      if (!changeCount) return;
      setActiveChange((current) => (current + direction + changeCount) % changeCount);
    },
    [activeChange, changeCount, groupSelectedIndex, openGroupFile, selectionMode],
  );

  const triggerImpact = useCallback(() => {
    setImpact(true);
    window.setTimeout(() => setImpact(false), 700);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.closest("button, dialog, input, textarea")) return;
      const key = event.key.toLowerCase();
      if (key === "j") navigateChange(1);
      if (key === "k") navigateChange(-1);
      if (selectionMode === "group" && key === "]") stepGroupFile(1);
      if (selectionMode === "group" && key === "[") stepGroupFile(-1);
      if (key === "t") toggle();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigateChange, selectionMode, stepGroupFile, toggle]);

  const complete = async () => {
    setBusy(true);
    try {
      if (selectionMode === "group") {
        const group = await api.completeGroup(groupId);
        setGroupSummary(group);
        setGroupItems((current) =>
          current.map((item) => ({
            ...item,
            patch:
              group.patches.find(
                (patch) => patch.transaction_id === item.patch.transaction_id,
              ) ?? item.patch,
          })),
        );
        await loadGroups();
        triggerImpact();
        toast("Review completo marcado como revisado. O rollback continua disponível.");
      } else {
        const patch = await api.completePatch(groupId, transactionId);
        setPatchSummary(patch);
        await loadGroups();
        await loadSummary(groupId, transactionId, selectedIndex);
        triggerImpact();
        toast("Atualização marcada como revisada. O rollback continua disponível.");
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : "Falha ao concluir", true);
    } finally {
      setBusy(false);
    }
  };

  const rollbackPatch = async () => {
    setDialog(null);
    setBusy(true);
    try {
      const patch = await api.rollbackPatch(groupId, transactionId);
      setPatchSummary(patch);
      await loadGroups();
      await loadSummary(groupId, transactionId, selectedIndex);
      toast("Atualização desfeita e snapshots anteriores restaurados.");
    } catch (err) {
      const conflicts = err instanceof ApiError ? err.payload.conflicts : undefined;
      toast(
        conflicts?.length
          ? `Rollback bloqueado: ${conflicts.join(", ")} mudou depois da transação.`
          : err instanceof Error
            ? err.message
            : "Falha no rollback",
        true,
      );
    } finally {
      setBusy(false);
    }
  };

  const rollbackGroup = async () => {
    setDialog(null);
    setBusy(true);
    try {
      await api.rollbackGroup(groupId);
      await loadGroups();
      if (selectionMode === "group") {
        await loadGroupOverview(groupId);
      } else {
        await loadSummary(groupId, transactionId, selectedIndex);
      }
      toast("Review desfeito em ordem reversa.");
    } catch (err) {
      const conflicts = err instanceof ApiError ? err.payload.conflicts : undefined;
      toast(
        conflicts?.length
          ? `Rollback bloqueado: ${conflicts.join(", ")}.`
          : err instanceof Error
            ? err.message
            : "Falha no rollback",
        true,
      );
    } finally {
      setBusy(false);
    }
  };

  const groupRolledBack = Boolean(
    groupSummary && groupSummary.rolled_back_count === groupSummary.patches_count,
  );
  const groupReviewed = Boolean(groupSummary && !groupSummary.pending_count && !groupRolledBack);
  const rolledBack =
    selectionMode === "group" ? groupRolledBack : patchSummary?.status === "rolled_back";
  const reviewed =
    selectionMode === "group" ? groupReviewed : patchSummary?.review_state === "reviewed";
  const totalAdditions =
    selectionMode === "group" ? groupSummary?.additions ?? 0 : patchSummary?.additions ?? 0;
  const totalDeletions =
    selectionMode === "group" ? groupSummary?.deletions ?? 0 : patchSummary?.deletions ?? 0;
  const appliedCount = groupSummary
    ? groupSummary.patches_count - groupSummary.rolled_back_count
    : 0;

  if (offline) {
    return (
      <div className="app">
        <header className="topbar">
          <div className="brand" aria-hidden="true">
            <ReviewIcon size={17} />
          </div>
          <div className="heading">
            <div className="heading-row">
              <h1>Abra pela revisão local</h1>
              <span className="pill muted">Sem sessão</span>
            </div>
          </div>
        </header>
        <main className="workspace">
          <div className="panel">
            <div className="empty">
              <strong>Este arquivo não funciona sozinho</strong>
              <p>
                Suba o MCP ou execute “code-harness review” e abra http://127.0.0.1:8765
                (ou a porta em CODE_HARNESS_REVIEW_PORT).
              </p>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className={`app${impact ? " impact-flash" : ""}`}>
      <header className="topbar">
        <div className="brand" aria-hidden="true">
          <ReviewIcon size={17} />
        </div>
        <div className="heading">
          <div className="heading-row">
            <h1 id="review-title">
              {groupSummary?.group_title ??
                (groups.length ? "Carregando review…" : "Nenhuma alteração ainda")}
            </h1>
            <span
              className={`pill${rolledBack ? " muted" : reviewed ? " ok" : ""}`}
              id="review-state"
            >
              {rolledBack ? "Desfeito" : reviewed ? "Revisado" : "Não revisado"}
            </span>
          </div>
          <div className="meta">
            <span className="meta-primary" id="patch-label">
              {selectionMode === "group"
                ? `${groupSummary?.patches_count ?? 0} atualizações · ${groupSummary?.files_changed ?? 0} arquivos`
                : patchSummary?.description ?? "Atualização"}
            </span>
            <span id="source-tool">
              {selectionMode === "group"
                ? "Visão geral"
                : patchSummary
                  ? formatSource(patchSummary.source_tool)
                  : "—"}
            </span>
            <span className="dot" aria-hidden="true" />
            <time
              id="created-at"
              dateTime={selectionMode === "group" ? groupSummary?.updated_at : patchSummary?.created_at}
            >
              {selectionMode === "group"
                ? groupSummary
                  ? formatDate(groupSummary.updated_at)
                  : "—"
                : patchSummary
                  ? formatDate(patchSummary.created_at)
                  : "—"}
            </time>
            <span className="dot" aria-hidden="true" />
            <span id="patch-progress">
              {groupSummary
                ? `${groupSummary.reviewed_count}/${groupSummary.patches_count} revisadas`
                : "0 atualizações"}
            </span>
            <span className="stats diff-total">
              <b className="add" id="total-additions">
                +{totalAdditions}
              </b>
              <b className="del" id="total-deletions">
                −{totalDeletions}
              </b>
            </span>
          </div>
        </div>
        <div className="actions">
          <button
            className="icon-btn"
            id="theme-toggle"
            type="button"
            aria-label={theme === "dark" ? "Usar tema claro" : "Usar tema escuro"}
            onClick={toggle}
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
          <button
            className="btn ghost-label"
            id="rollback-review-button"
            type="button"
            disabled={busy || !groupSummary || groupSummary.rolled_back_count === groupSummary.patches_count}
            onClick={() => setDialog("group")}
          >
            <UndoIcon />
            <span className="label">Review</span>
          </button>
          {selectionMode === "file" && (
            <button
              className="btn ghost-label"
              id="rollback-button"
              type="button"
              disabled={busy || !patchSummary || rolledBack}
              onClick={() => setDialog("patch")}
            >
              <HistoryIcon />
              <span className="label">Atualização</span>
            </button>
          )}
          <button
            className="btn primary"
            id="complete-button"
            type="button"
            disabled={
              busy ||
              (selectionMode === "group" ? !groupSummary : !patchSummary) ||
              reviewed ||
              rolledBack
            }
            onClick={complete}
          >
            {!reviewed && <CheckIcon />}
            {selectionMode === "group"
              ? reviewed
                ? "Review aprovado"
                : "Aprovar review"
              : reviewed
                ? "Atualização revisada"
                : "Concluir atualização"}
          </button>
        </div>
      </header>

      <main className="workspace">
        <Sidebar
          groups={groups}
          total={total}
          groupId={groupId}
          transactionId={transactionId}
          selectedIndex={selectionMode === "group" ? activeGroupItem?.file.index ?? 0 : selectedIndex}
          groupOverview={selectionMode === "group"}
          expandedGroups={expandedGroups}
          expandedPatches={expandedPatches}
          onToggleGroup={(id) =>
            setExpandedGroups((prev) => {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            })
          }
          onTogglePatch={(id) =>
            setExpandedPatches((prev) => {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            })
          }
          onSelectGroup={selectGroup}
          onSelectPatch={(g, t) => selectPatch(g, t, 0)}
          onSelectFile={(g, t, i) => {
            if (selectionMode === "group" && g === groupId) {
              const queueIndex = groupItems.findIndex(
                (item) => item.patch.transaction_id === t && item.file.index === i,
              );
              if (queueIndex >= 0) {
                openGroupFile(queueIndex);
                return;
              }
            }
            void selectPatch(g, t, i);
          }}
        />

        <section
          className="panel"
          aria-label={selectionMode === "group" ? "Comparação completa do review" : "Comparação do arquivo"}
        >
          <div className="toolbar">
            <div className="identity">
              <span
                className={`pill${fileDiff ? ` ${fileDiff.operation}` : ""}`}
                id="operation-badge"
              >
                {fileDiff ? operationLabel(fileDiff.operation) : "Modificado"}
              </span>
              <strong
                id="selected-path"
                title={selectionMode === "group" ? activeGroupItem?.file.path : fileDiff?.path}
              >
                {selectionMode === "group"
                  ? activeGroupItem
                    ? `${groupSelectedIndex + 1}/${groupItems.length} · ${activeGroupItem.file.path}`
                    : "Selecione um arquivo"
                  : fileDiff?.path ?? "Selecione um arquivo"}
              </strong>
              {fileDiff && (
                <span className="file-stats" aria-label="Resumo de linhas alteradas">
                  <b className="add">+{fileDiff.additions}</b>
                  <b className="del">−{fileDiff.deletions}</b>
                </span>
              )}
            </div>
            <div className="tools">
              {selectionMode === "group" && (
                <div className="nav-files" role="group" aria-label="Navegar entre arquivos">
                  <button
                    className="compact"
                    type="button"
                    aria-label="Arquivo anterior"
                    onClick={() => stepGroupFile(-1)}
                  >
                    <ChevronLeftIcon size={14} />
                  </button>
                  <span>{groupItems.length ? `${groupSelectedIndex + 1} de ${groupItems.length}` : "0 de 0"}</span>
                  <button
                    className="compact"
                    type="button"
                    aria-label="Próximo arquivo"
                    onClick={() => stepGroupFile(1)}
                  >
                    <ChevronRightIcon size={14} />
                  </button>
                </div>
              )}
              <div className="nav-changes" role="group" aria-label="Navegar entre alterações">
                <button
                  className="compact"
                  id="previous-change"
                  type="button"
                  aria-label="Alteração anterior"
                  onClick={() => navigateChange(-1)}
                >
                  <ChevronLeftIcon size={14} />
                </button>
                <span id="change-position">
                  {changeCount ? `${Math.max(1, activeChange + 1)} de ${changeCount}` : "0 de 0"}
                </span>
                <button
                  className="compact"
                  id="next-change"
                  type="button"
                  aria-label="Próxima alteração"
                  onClick={() => navigateChange(1)}
                >
                  <ChevronRightIcon size={14} />
                </button>
              </div>
              <div className="switcher" role="group" aria-label="Modo de comparação">
                <button
                  type="button"
                  className={view === "split" ? "active" : ""}
                  data-view="split"
                  onClick={() => setView("split")}
                >
                  Lado a lado
                </button>
                <button
                  type="button"
                  className={view === "unified" ? "active" : ""}
                  data-view="unified"
                  onClick={() => setView("unified")}
                >
                  Unificado
                </button>
              </div>
            </div>
          </div>

          <div
            className="diff-scroll"
            id="diff-scroller"
            tabIndex={0}
            aria-label={selectionMode === "group" ? "Diff completo do review" : "Diff do arquivo"}
          >
            {error ? (
              <div className="empty">
                <strong>Não foi possível carregar o diff</strong>
                <p>{error}</p>
              </div>
            ) : (
              <MonacoDiffPane
                file={fileDiff}
                loading={loadingFile || loadingGroup}
                view={view}
                theme={theme}
                activeChange={activeChange}
                onNavigateRequest={setActiveChange}
              />
            )}
          </div>

        </section>
      </main>

      <div className="toast-region" id="toast-region" aria-live="polite">
        {toasts.map((item) => (
          <div key={item.id} className={`toast${item.error ? " error" : ""}`}>
            {item.message}
          </div>
        ))}
      </div>

      {dialog && (
        <div className="modal-backdrop" role="presentation" onClick={() => setDialog(null)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-icon" aria-hidden="true">
              <AlertIcon size={20} />
            </div>
            <h2>{dialog === "patch" ? "Desfazer esta atualização?" : "Desfazer todo o review?"}</h2>
            <p id={dialog === "group" ? "rollback-review-detail" : undefined}>
              {dialog === "patch"
                ? "Os arquivos voltarão aos snapshots anteriores. Atualizações posteriores bloquearão a operação."
                : `${appliedCount} ${appliedCount === 1 ? "atualização aplicada" : "atualizações aplicadas"} e ${groupSummary?.files_changed ?? 0} ${groupSummary?.files_changed === 1 ? "arquivo" : "arquivos"} serão validados antes de qualquer escrita.`}
            </p>
            <div className="modal-actions">
              <button className="btn" type="button" onClick={() => setDialog(null)}>
                Cancelar
              </button>
              <button
                className="btn danger"
                type="button"
                id={dialog === "patch" ? "confirm-rollback" : "confirm-review-rollback"}
                onClick={dialog === "patch" ? rollbackPatch : rollbackGroup}
              >
                {dialog === "patch" ? "Desfazer atualização" : "Desfazer review"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
