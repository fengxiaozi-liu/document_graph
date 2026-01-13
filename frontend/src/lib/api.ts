export type Workspace = {
  id: string;
  name: string;
  qdrant_collection: string;
  qdrant_alias?: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskSummary = {
  id: string;
  status: string;
  stage?: string | null;
  progress?: number | null;
  error?: unknown;
  updated_at?: string | null;
};

export type DocumentListItem = {
  id: string;
  title: string;
  external_key: string;
  updated_at: string;
  latest_task?: TaskSummary | null;
};

export type DocumentPreview = {
  document_id: string;
  title: string;
  file_ext?: string | null;
  mime_type?: string | null;
  content: string;
  content_type: "markdown" | "text";
  truncated: boolean;
};

export type Task = {
  id: string;
  workspace_id: string;
  document_id?: string | null;
  type: string;
  status: string;
  stage?: string | null;
  progress?: number | null;
  error: unknown;
  result: unknown;
  attempt: number;
  max_attempts: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at: string;
};

const DEFAULT_BASE = "http://localhost:8000";

export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_BASE;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${apiBaseUrl()}${path}`;
  const resp = await fetch(url, {
    ...init,
    headers: {
      ...(init?.headers || {}),
    },
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  return (await resp.json()) as T;
}

export async function listWorkspaces(): Promise<Workspace[]> {
  return request<Workspace[]>("/workspaces");
}

export async function createWorkspace(payload: {
  name: string;
  qdrant_alias?: string | null;
}): Promise<Workspace> {
  return request<Workspace>("/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listDocuments(
  workspaceId: string,
  opts?: { limit?: number; offset?: number },
): Promise<DocumentListItem[]> {
  const limit = opts?.limit ?? 10;
  const offset = opts?.offset ?? 0;
  return request<DocumentListItem[]>(
    `/workspaces/${workspaceId}/documents?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
  );
}

export async function uploadDocument(workspaceId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<{
    workspace_id: string;
    document_id: string;
    task_id: string;
    task_status: string;
  }>(`/workspaces/${workspaceId}/documents/upload`, {
    method: "POST",
    body: form,
  });
}

export async function getTask(taskId: string): Promise<Task> {
  return request<Task>(`/tasks/${taskId}`);
}

export async function getDocumentPreview(
  workspaceId: string,
  documentId: string,
): Promise<DocumentPreview> {
  return request<DocumentPreview>(
    `/workspaces/${workspaceId}/documents/${documentId}/preview`,
  );
}

export async function chat(
  workspaceId: string,
  payload: { conversation_id?: string | null; message: string; top_k?: number },
): Promise<{ conversation_id: string; answer: string; refs: unknown[] }> {
  return request(`/workspaces/${workspaceId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
