export type ReviewState = "pending" | "reviewed";
export type PatchStatus = "applied" | "rolled_back" | string;
export type FileOperation = "modify" | "create" | "delete" | string;
export type DiffKind = "context" | "addition" | "deletion" | "replacement" | "collapsed";
export type DiffView = "split" | "unified";

export interface ReviewFileSummary {
  index: number;
  path: string;
  operation: FileOperation;
  additions: number;
  deletions: number;
  binary: boolean;
}

export interface PatchListItem {
  transaction_id: string;
  source_tool: string;
  status: PatchStatus;
  review_state: ReviewState | string;
  created_at: string;
  reviewed_at: string | null;
  description: string;
  files_changed: number;
  additions: number;
  deletions: number;
  files: ReviewFileSummary[];
}

export interface PatchGroupListItem {
  group_id: string;
  group_title: string;
  created_at: string;
  updated_at: string;
  legacy: boolean;
  patches_count: number;
  files_changed: number;
  additions: number | null;
  deletions: number | null;
  reviewed_count: number;
  pending_count: number;
  rolled_back_count: number;
  patches: PatchListItem[];
}

export interface PatchGroupList {
  items: PatchGroupListItem[];
  total: number;
  retained_limit: number;
}

export interface PatchGroupSummary extends PatchGroupListItem {}

export interface PatchSummary extends PatchListItem {
  group_id?: string;
}

export interface SideBySideRow {
  kind: DiffKind;
  old_line_number: number | null;
  old_text: string | null;
  new_line_number: number | null;
  new_text: string | null;
  hidden_lines?: number;
  old_start?: number;
  new_start?: number;
}

export interface ReviewFileDiff {
  index: number;
  path: string;
  operation: FileOperation;
  additions: number;
  deletions: number;
  binary: boolean;
  rows: SideBySideRow[];
}

export interface ApiErrorBody {
  error?: string;
  code?: string;
  conflicts?: string[];
}
