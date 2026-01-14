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
  content_type: "markdown" | "text" | "image";
  truncated: boolean;
  download_url: string;
  view_url: string;
};

export type MessageItem = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool" | string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type MessageListResponse = {
  items: MessageItem[];
  next_before?: string | null;
};

export type UploadManyItem = {
  relative_path: string;
  document_id: string;
  task_id: string;
  task_status: string;
};

export type UploadManyResponse = {
  workspace_id: string;
  items: UploadManyItem[];
};

export type DocumentTreeNode = {
  type: "folder" | "file";
  name: string;
  path: string;
  children?: DocumentTreeNode[] | null;
  document?: {
    id: string;
    title: string;
    external_key: string;
    updated_at?: string | null;
    latest_task?: TaskSummary | null;
  } | null;
};

export type DeleteManyResponse = {
  deleted_documents: number;
  deleted_files: number;
  deleted_vectors: number;
  revoked_tasks: number;
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

export async function uploadMany(
  workspaceId: string,
  files: File[],
  relativePaths: string[],
): Promise<UploadManyResponse> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  relativePaths.forEach((p) => form.append("relative_paths", p));
  return request<UploadManyResponse>(`/workspaces/${workspaceId}/documents/upload_many`, {
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

export async function listConversationMessages(
  conversationId: string,
  opts?: { limit?: number; before?: string | null },
): Promise<MessageListResponse> {
  const limit = opts?.limit ?? 50;
  const before = opts?.before ?? null;
  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  if (before) qs.set("before", before);
  return request<MessageListResponse>(`/conversations/${conversationId}/messages?${qs.toString()}`);
}

export async function getDocumentTree(
  workspaceId: string,
  opts?: { prefix?: string | null },
): Promise<DocumentTreeNode> {
  const qs = new URLSearchParams();
  if (opts?.prefix) qs.set("prefix", opts.prefix);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<DocumentTreeNode>(`/workspaces/${workspaceId}/documents/tree${suffix}`);
}

export async function deleteDocument(
  workspaceId: string,
  documentId: string,
): Promise<DeleteManyResponse> {
  return request<DeleteManyResponse>(`/workspaces/${workspaceId}/documents/${documentId}`, {
    method: "DELETE",
  });
}

export async function deleteByPrefix(
  workspaceId: string,
  prefix: string,
): Promise<DeleteManyResponse> {
  const qs = new URLSearchParams();
  qs.set("prefix", prefix);
  return request<DeleteManyResponse>(`/workspaces/${workspaceId}/documents/by_prefix?${qs.toString()}`, {
    method: "DELETE",
  });
}

export async function deleteMany(
  workspaceId: string,
  payload: { document_ids?: string[]; prefixes?: string[] },
): Promise<DeleteManyResponse> {
  return request<DeleteManyResponse>(`/workspaces/${workspaceId}/documents/delete_many`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
