import type {
  ApiErrorBody,
  PatchGroupList,
  PatchGroupSummary,
  PatchSummary,
  ReviewFileDiff,
} from "./types";

function meta(name: string): string {
  return document.querySelector(`meta[name="${name}"]`)?.getAttribute("content") ?? "";
}

export const bootstrap = {
  csrfToken: meta("review-csrf"),
};

export class ApiError extends Error {
  payload: ApiErrorBody;

  constructor(message: string, payload: ApiErrorBody = {}) {
    super(message);
    this.payload = payload;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.method === "POST") {
    headers.set("X-CSRF-Token", bootstrap.csrfToken);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  let payload: ApiErrorBody & T = {} as ApiErrorBody & T;
  try {
    payload = (await response.json()) as ApiErrorBody & T;
  } catch {
    payload = { error: "A resposta local não pôde ser lida." } as ApiErrorBody & T;
  }
  if (!response.ok) {
    throw new ApiError(payload.error || "A operação não pôde ser concluída.", payload);
  }
  return payload;
}

export const api = {
  listGroups: (limit = 200) => request<PatchGroupList>(`/api/groups?limit=${limit}`),
  getGroup: (groupId: string) =>
    request<PatchGroupSummary>(`/api/groups/${encodeURIComponent(groupId)}`),
  getPatch: (groupId: string, transactionId: string) =>
    request<PatchSummary>(
      `/api/groups/${encodeURIComponent(groupId)}/patches/${encodeURIComponent(transactionId)}`,
    ),
  getFile: (groupId: string, transactionId: string, index: number, fullContext = false) => {
    const suffix = fullContext ? "?context=full" : "";
    return request<ReviewFileDiff>(
      `/api/groups/${encodeURIComponent(groupId)}/patches/${encodeURIComponent(transactionId)}/files/${index}${suffix}`,
    );
  },
  completePatch: (groupId: string, transactionId: string) =>
    request<PatchSummary>(
      `/api/groups/${encodeURIComponent(groupId)}/patches/${encodeURIComponent(transactionId)}/complete`,
      { method: "POST", body: "{}" },
    ),
  rollbackPatch: (groupId: string, transactionId: string) =>
    request<PatchSummary>(
      `/api/groups/${encodeURIComponent(groupId)}/patches/${encodeURIComponent(transactionId)}/rollback`,
      { method: "POST", body: "{}" },
    ),
  completeGroup: (groupId: string) =>
    request<PatchGroupSummary>(`/api/groups/${encodeURIComponent(groupId)}/complete`, {
      method: "POST",
      body: "{}",
    }),
  rollbackGroup: (groupId: string) =>
    request<PatchGroupSummary>(`/api/groups/${encodeURIComponent(groupId)}/rollback`, {
      method: "POST",
      body: "{}",
    }),
};
