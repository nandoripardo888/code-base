import type { PatchGroupListItem } from "../types";
import { ChevronRightIcon, FileIcon, FolderIcon, ReviewIcon } from "./Icons";
import { fileNameParts, groupStatus, operationLabel, patchStatus } from "../utils";

export function Sidebar({
  groups,
  total,
  groupId,
  transactionId,
  selectedIndex,
  groupOverview,
  expandedGroups: _expandedGroups,
  expandedPatches,
  onToggleGroup: _onToggleGroup,
  onTogglePatch,
  onSelectGroup,
  onSelectPatch,
  onSelectFile,
}: {
  groups: PatchGroupListItem[];
  total: number;
  groupId: string;
  transactionId: string;
  selectedIndex: number;
  groupOverview: boolean;
  expandedGroups: Set<string>;
  expandedPatches: Set<string>;
  onToggleGroup: (id: string) => void;
  onTogglePatch: (id: string) => void;
  onSelectGroup: (id: string) => void;
  onSelectPatch: (groupId: string, transactionId: string) => void;
  onSelectFile: (groupId: string, transactionId: string, fileIndex: number) => void;
}) {
  const activeGroup = groups.find((group) => group.group_id === groupId) ?? groups[0] ?? null;

  return (
    <aside className="sidebar ide-explorer" aria-label="Explorer de arquivos do review">
      <div className="explorer-titlebar">
        <div>
          <ReviewIcon size={14} />
          <strong>EXPLORER</strong>
        </div>
        <span className="explorer-total" title={`${total} reviews disponíveis`}>
          {total}
        </span>
      </div>

      <label className="review-picker-label" htmlFor="review-picker">
        Review atual
      </label>
      <select
        id="review-picker"
        className="review-picker"
        value={activeGroup?.group_id ?? ""}
        disabled={!groups.length}
        onChange={(event) => onSelectGroup(event.target.value)}
      >
        {groups.map((group) => (
          <option key={group.group_id} value={group.group_id}>
            {group.group_title}
          </option>
        ))}
      </select>

      {activeGroup ? (
        <nav className="compact-tree" role="tree" aria-label="Atualizações e arquivos do review atual">
          <button
            type="button"
            className={`explorer-overview${groupOverview ? " active" : ""}`}
            role="treeitem"
            aria-selected={groupOverview}
            onClick={() => onSelectGroup(activeGroup.group_id)}
          >
            <span className={`status-dot ${groupStatus(activeGroup)}`} aria-hidden="true" />
            <span className="explorer-label-copy">
              <strong title={activeGroup.group_title}>{activeGroup.group_title}</strong>
              <small>
                {activeGroup.patches_count} atualizações · {activeGroup.files_changed} arquivos
              </small>
            </span>
          </button>

          <div className="explorer-section-label">CHANGES</div>

          {activeGroup.patches.map((patch) => {
            const expanded = expandedPatches.has(patch.transaction_id);
            const patchActive = patch.transaction_id === transactionId;
            const status = patchStatus(patch);

            return (
              <div className="explorer-folder" key={patch.transaction_id}>
                <div className={`explorer-folder-row${patchActive ? " active" : ""}`}>
                  <button
                    type="button"
                    className={`explorer-chevron${expanded ? " expanded" : ""}`}
                    aria-label={expanded ? "Recolher atualização" : "Expandir atualização"}
                    aria-expanded={expanded}
                    onClick={() => onTogglePatch(patch.transaction_id)}
                  >
                    <ChevronRightIcon size={12} />
                  </button>
                  <FolderIcon size={14} className="folder-icon" />
                  <button
                    type="button"
                    className="explorer-folder-name"
                    role="treeitem"
                    aria-selected={patchActive && !groupOverview}
                    title={patch.description}
                    onClick={() => onSelectPatch(activeGroup.group_id, patch.transaction_id)}
                  >
                    {patch.description}
                  </button>
                  <span className={`status-dot ${status}`} title={status} aria-hidden="true" />
                  <span className="folder-count">{patch.files.length}</span>
                </div>

                {expanded && (
                  <div className="explorer-files" role="group">
                    {patch.files.map((file) => {
                      const parts = fileNameParts(file.path);
                      const fileActive = patchActive && file.index === selectedIndex;
                      return (
                        <button
                          key={`${patch.transaction_id}-${file.index}`}
                          type="button"
                          className={`explorer-file${fileActive ? " active" : ""}`}
                          role="treeitem"
                          aria-selected={fileActive}
                          title={file.path}
                          onClick={() =>
                            onSelectFile(activeGroup.group_id, patch.transaction_id, file.index)
                          }
                        >
                          <FileIcon size={13} className={`file-icon ${file.operation}`} />
                          <span className="explorer-file-name">{parts.name}</span>
                          <span className="explorer-file-path">{parts.dir}</span>
                          <span className={`file-op-mark ${file.operation}`} title={operationLabel(file.operation)}>
                            {file.operation === "create" ? "A" : file.operation === "delete" ? "D" : "M"}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>
      ) : (
        <div className="explorer-empty">Nenhum review disponível.</div>
      )}
    </aside>
  );
}
