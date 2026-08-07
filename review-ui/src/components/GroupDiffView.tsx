import type { CSSProperties } from "react";
import { ChevronLeftIcon, ChevronRightIcon } from "./Icons";
import { MonacoDiffPane } from "./DiffView";
import type {
  DiffView,
  PatchListItem,
  ReviewFileDiff,
  ReviewFileSummary,
} from "../types";
import { formatSource, operationLabel, patchStatus } from "../utils";

export interface ReviewQueueItem {
  key: string;
  patch: PatchListItem;
  patchIndex: number;
  file: ReviewFileSummary;
}

function impactScore(item: ReviewQueueItem): number {
  return Math.max(1, item.file.additions + item.file.deletions);
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

export function GroupDiffView({
  items,
  selectedIndex,
  selectedFile,
  loading,
  view,
  theme,
  activeChange,
  onNavigateRequest,
  onSelectFile,
  onStepFile,
}: {
  items: ReviewQueueItem[];
  selectedIndex: number;
  selectedFile: ReviewFileDiff | null;
  loading: boolean;
  view: DiffView;
  theme: "light" | "dark";
  activeChange: number;
  onNavigateRequest?: (index: number) => void;
  onSelectFile: (index: number) => void;
  onStepFile: (direction: number) => void;
}) {
  const selected = items[selectedIndex] ?? null;
  const maxImpact = items.reduce((max, item) => Math.max(max, impactScore(item)), 1);
  const selectedImpact = selected ? impactScore(selected) : 0;
  const selectedStatus = selected ? patchStatus(selected.patch) : "pendente";

  if (!items.length) {
    return (
      <div className="loading" aria-busy={loading}>
        <div className="volt-pulse" />
        <span>{loading ? "Mapeando o impacto do review..." : "Nenhum arquivo neste review."}</span>
      </div>
    );
  }

  return (
    <div className="review-flight-deck">
      <header className="impact-header">
        <div className="impact-title">
          <span className="impact-kicker">Impact map</span>
          <strong>
            {selectedIndex + 1}/{items.length} - {selected ? basename(selected.file.path) : "-"}
          </strong>
          <span>
            {selected
              ? `${selected.patchIndex + 1}. ${selected.patch.description}`
              : "Selecione um arquivo"}
          </span>
        </div>
        <div className="impact-metrics">
          <span className={`tree-status ${selectedStatus}`}>{selectedStatus}</span>
          <span>{selectedImpact} linhas em movimento</span>
          <span className="keyboard-hint">J/K alteracoes - [/] arquivos</span>
        </div>
      </header>

      <div className="impact-track" aria-label="Mapa de impacto dos arquivos">
        {items.map((item, index) => {
          const heat = Math.max(0.16, impactScore(item) / maxImpact);
          return (
            <button
              key={item.key}
              type="button"
              className={`impact-node${index === selectedIndex ? " active" : ""}`}
              style={
                {
                  "--impact-opacity": 0.28 + heat * 0.72,
                  "--impact-scale": 0.34 + heat * 0.66,
                } as CSSProperties
              }
              aria-label={`${index + 1}. ${item.file.path}`}
              aria-current={index === selectedIndex ? "true" : undefined}
              title={`${item.file.path} - +${item.file.additions} -${item.file.deletions}`}
              onClick={() => onSelectFile(index)}
            />
          );
        })}
      </div>

      <div className="review-flight-body">
        <aside className="review-queue" aria-label="Fila de arquivos do review">
          <div className="review-queue-head">
            <div>
              <span>Flight queue</span>
              <strong>Hotspots do review</strong>
            </div>
            <span className="count">{items.length}</span>
          </div>
          <div className="review-queue-list">
            {items.map((item, index) => {
              const startsPatch = index === 0 || items[index - 1].patch.transaction_id !== item.patch.transaction_id;
              const heat = Math.max(0.16, impactScore(item) / maxImpact);
              return (
                <div className="queue-cluster" key={item.key}>
                  {startsPatch && (
                    <div className="queue-patch-label">
                      <span>{String(item.patchIndex + 1).padStart(2, "0")}</span>
                      <div>
                        <strong>{item.patch.description}</strong>
                        <small>
                          {formatSource(item.patch.source_tool)} - {item.patch.files.length} arquivos
                        </small>
                      </div>
                    </div>
                  )}
                  <button
                    type="button"
                    className={`queue-file${index === selectedIndex ? " active" : ""}`}
                    aria-current={index === selectedIndex ? "true" : undefined}
                    onClick={() => onSelectFile(index)}
                  >
                    <span
                      className="queue-heat"
                      style={{ "--impact-height": `${16 + heat * 24}px` } as CSSProperties}
                      aria-hidden="true"
                    />
                    <span className="queue-file-copy">
                      <strong title={item.file.path}>{basename(item.file.path)}</strong>
                      <small title={item.file.path}>{item.file.path}</small>
                    </span>
                    <span className="queue-file-meta">
                      <span className={`pill ${item.file.operation}`}>
                        {operationLabel(item.file.operation)}
                      </span>
                      <span className="file-stats">
                        <b className="add">+{item.file.additions}</b>
                        <b className="del">-{item.file.deletions}</b>
                      </span>
                    </span>
                  </button>
                </div>
              );
            })}
          </div>
        </aside>

        <section className="review-focus">
          <div className="review-focus-head">
            <div className="focus-identity">
              <span className="focus-orbit" aria-hidden="true" />
              <div>
                <span>Focus stream</span>
                <strong title={selected?.file.path}>{selected?.file.path ?? "Selecione um arquivo"}</strong>
              </div>
            </div>
            <div className="focus-file-nav" role="group" aria-label="Navegar entre arquivos">
              <button type="button" onClick={() => onStepFile(-1)} aria-label="Arquivo anterior">
                <ChevronLeftIcon size={14} />
                <span>Anterior</span>
              </button>
              <span>{selectedIndex + 1} de {items.length}</span>
              <button type="button" onClick={() => onStepFile(1)} aria-label="Proximo arquivo">
                <span>Proximo</span>
                <ChevronRightIcon size={14} />
              </button>
            </div>
          </div>
          <div className="review-monaco">
            <MonacoDiffPane
              file={selectedFile}
              loading={loading}
              view={view}
              theme={theme}
              activeChange={activeChange}
              onNavigateRequest={onNavigateRequest}
            />
          </div>
        </section>
      </div>
    </div>
  );
}
