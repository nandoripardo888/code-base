import type { FileOperation, PatchGroupListItem, PatchListItem } from "./types";

export function formatSource(value: string): string {
  return String(value)
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}

export function formatDate(value: string): string {
  try {
    return new Intl.DateTimeFormat("pt-BR", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export function operationLabel(operation: FileOperation): string {
  if (operation === "modify") return "Modificado";
  if (operation === "create") return "Criado";
  if (operation === "delete") return "Excluído";
  return String(operation);
}

export function extensionLabel(filename: string): string {
  const extension = filename.includes(".") ? filename.split(".").pop() ?? "" : "";
  return extension.slice(0, 3).toUpperCase() || "•";
}

export function fileNameParts(path: string): { name: string; dir: string } {
  const parts = path.split("/");
  const name = parts.pop() || path;
  return { name, dir: parts.join("/") || "raiz do projeto" };
}

export function groupStatus(group: PatchGroupListItem): "pendente" | "revisado" | "desfeito" {
  if (group.rolled_back_count === group.patches_count) return "desfeito";
  if (group.pending_count) return "pendente";
  return "revisado";
}

export function patchStatus(patch: PatchListItem): "pendente" | "revisada" | "desfeita" {
  if (patch.status === "rolled_back") return "desfeita";
  return patch.review_state === "reviewed" ? "revisada" : "pendente";
}

export function readUrlState(): { group?: string; patch?: string; file: number; scope?: "group" } {
  const url = new URL(window.location.href);
  const file = Number.parseInt(url.searchParams.get("file") || "0", 10);
  return {
    group: url.searchParams.get("group") || undefined,
    patch: url.searchParams.get("patch") || undefined,
    file: Number.isInteger(file) ? file : 0,
    scope: url.searchParams.get("scope") === "group" ? "group" : undefined,
  };
}

export function writeUrlState(groupId: string, transactionId: string, fileIndex: number): void {
  const url = new URL(window.location.href);
  url.searchParams.set("group", groupId);
  url.searchParams.set("patch", transactionId);
  url.searchParams.set("file", String(fileIndex));
  url.searchParams.delete("scope");
  window.history.replaceState({}, "", url);
}

export function writeGroupUrlState(groupId: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("group", groupId);
  url.searchParams.set("scope", "group");
  url.searchParams.delete("patch");
  url.searchParams.delete("file");
  window.history.replaceState({}, "", url);
}
