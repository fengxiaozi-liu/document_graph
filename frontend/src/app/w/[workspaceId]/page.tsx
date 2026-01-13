"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  apiBaseUrl,
  chat,
  getTask,
  getDocumentPreview,
  listDocuments,
  uploadDocument,
  type DocumentPreview,
  type DocumentListItem,
} from "@/lib/api";

type RefItem = unknown;

function errToString(e: unknown): string {
  if (e instanceof Error) return e.message;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

function conversationKey(workspaceId: string) {
  return `current_conversation:${workspaceId}`;
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ workspaceId: string }>();
  const workspaceId = params.workspaceId;

  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [docOffset, setDocOffset] = useState(0);
  const docLimit = 10;
  const [docLoading, setDocLoading] = useState(true);
  const [docError, setDocError] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [activeIndexTasks, setActiveIndexTasks] = useState<
    Record<
      string,
      {
        taskId: string;
        status: string;
        stage?: string | null;
        progress?: number | null;
        error?: unknown;
      }
    >
  >({});

  const [messages, setMessages] = useState<Array<{ role: "user" | "assistant"; content: string; refs?: RefItem[] }>>(
    [],
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<DocumentPreview | null>(null);

  useEffect(() => {
    try {
      const v = window.localStorage.getItem(conversationKey(workspaceId));
      if (v) setConversationId(v);
    } catch {
      // ignore
    }
  }, [workspaceId]);

  async function refreshDocuments() {
    setDocLoading(true);
    setDocError(null);
    try {
      const rows = await listDocuments(workspaceId, {
        limit: docLimit,
        offset: docOffset,
      });
      setDocuments(rows);
    } catch (e: unknown) {
      setDocError(errToString(e));
    } finally {
      setDocLoading(false);
    }
  }

  useEffect(() => {
    refreshDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, docOffset]);

  useEffect(() => {
    const taskIds = Object.values(activeIndexTasks)
      .map((t) => t.taskId)
      .filter(Boolean);
    if (taskIds.length === 0) return;

    const timer = window.setInterval(async () => {
      const entries = Object.entries(activeIndexTasks);
      if (entries.length === 0) return;

      const updates = await Promise.all(
        entries.map(async ([docId, t]) => {
          try {
            const latest = await getTask(t.taskId);
            return {
              docId,
              latest,
            };
          } catch {
            return { docId, latest: null as null };
          }
        }),
      );

      let shouldRefreshDocs = false;
      setActiveIndexTasks((prev) => {
        const next = { ...prev };
        for (const u of updates) {
          if (!u.latest) continue;
          const status = String(u.latest.status || "");
          next[u.docId] = {
            taskId: String(u.latest.id),
            status,
            stage: (u.latest.stage as string | null) ?? null,
            progress: (u.latest.progress as number | null) ?? null,
            error: u.latest.error,
          };
          if (status === "succeeded" || status === "failed" || status === "canceled") {
            delete next[u.docId];
            shouldRefreshDocs = true;
          }
        }
        return next;
      });

      if (shouldRefreshDocs) {
        await refreshDocuments();
      }
    }, 1500);

    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndexTasks, workspaceId, docOffset]);

  async function onUploadFile(file: File) {
    setUploading(true);
    try {
      const res = await uploadDocument(workspaceId, file);
      setActiveIndexTasks((prev) => ({
        ...prev,
        [res.document_id]: {
          taskId: res.task_id,
          status: res.task_status,
          stage: null,
          progress: null,
        },
      }));
      await refreshDocuments();
    } finally {
      setUploading(false);
    }
  }

  async function onSend() {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      const resp = await chat(workspaceId, {
        conversation_id: conversationId,
        message: text,
        top_k: 8,
      });
      setConversationId(resp.conversation_id);
      window.localStorage.setItem(
        conversationKey(workspaceId),
        resp.conversation_id,
      );
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: resp.answer, refs: resp.refs },
      ]);
    } catch (e: unknown) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `请求失败：${errToString(e)}`,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  async function onPreviewDocument(doc: DocumentListItem) {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewData(null);
    try {
      const data = await getDocumentPreview(workspaceId, doc.id);
      setPreviewData(data);
    } catch (e: unknown) {
      setPreviewError(errToString(e));
    } finally {
      setPreviewLoading(false);
    }
  }

  return (
    <div className="h-screen bg-zinc-50">
      <div className="grid h-full grid-cols-[320px_1fr] gap-3 p-3">
        <aside className="h-full overflow-hidden rounded-xl border border-zinc-200 bg-white">
          <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-3">
            <div className="text-sm font-semibold">来源（文件）</div>
            <Button
              size="sm"
              variant="secondary"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              {uploading ? "上传中…" : "上传"}
            </Button>
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              accept=".md,.txt,.html,.htm,.pdf,.docx"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onUploadFile(f);
                e.target.value = "";
              }}
            />
          </div>

          <div className="h-[calc(100%-52px)] overflow-auto p-3">
            {docLoading ? (
              <div className="text-sm text-zinc-500">加载中…</div>
            ) : docError ? (
              <div className="text-sm text-red-600">{docError}</div>
            ) : documents.length === 0 ? (
              <div className="text-sm text-zinc-500">暂无文件</div>
            ) : (
              <div className="space-y-2">
                {documents.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => void onPreviewDocument(d)}
                    className="block w-full text-left"
                  >
                    <Card className="p-3 hover:border-zinc-300 hover:shadow-sm">
                      <div className="truncate text-sm font-medium">
                        {d.title}
                      </div>
                      <div className="mt-1 text-xs text-zinc-500">
                        {activeIndexTasks[d.id]
                          ? `索引：${activeIndexTasks[d.id].status}${
                              activeIndexTasks[d.id].stage
                                ? ` · ${activeIndexTasks[d.id].stage}`
                                : ""
                            }`
                          : d.latest_task
                            ? `索引：${d.latest_task.status}${
                                d.latest_task.stage ? ` · ${d.latest_task.stage}` : ""
                              }`
                            : "索引：未开始"}
                      </div>
                      {(activeIndexTasks[d.id]?.status === "failed" ||
                        d.latest_task?.status === "failed") ? (
                        <div className="mt-1 text-xs text-red-600 truncate">
                          {JSON.stringify(
                            activeIndexTasks[d.id]?.error || d.latest_task?.error || {},
                          )}
                        </div>
                      ) : null}
                    </Card>
                  </button>
                ))}
              </div>
            )}

            <div className="mt-3 flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={docOffset === 0}
                onClick={() => setDocOffset((v) => Math.max(0, v - docLimit))}
              >
                上一页
              </Button>
              <div className="text-xs text-zinc-500">
                {docOffset + 1}–{docOffset + docLimit}
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={documents.length < docLimit}
                onClick={() => setDocOffset((v) => v + docLimit)}
              >
                下一页
              </Button>
            </div>
          </div>
        </aside>

        <main className="h-full overflow-hidden rounded-xl border border-zinc-200 bg-white">
          <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-3">
            <div className="text-sm font-semibold">对话</div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setConversationId(null);
                window.localStorage.removeItem(conversationKey(workspaceId));
                setMessages([]);
              }}
            >
              新对话
            </Button>
          </div>

          <div className="h-[calc(100%-112px)] overflow-auto p-4">
            {messages.length === 0 ? (
              <div className="text-sm text-zinc-500">开始提问…</div>
            ) : (
              <div className="space-y-4">
                {messages.map((m, idx) => (
                  <div key={idx} className="space-y-1">
                    <div className="text-xs font-semibold text-zinc-500">
                      {m.role === "user" ? "你" : "助手"}
                    </div>
                    <div className="whitespace-pre-wrap rounded-lg border border-zinc-100 bg-zinc-50 p-3 text-sm">
                      {m.content}
                    </div>
                    {m.role === "assistant" && m.refs && m.refs.length > 0 ? (
                      <details className="text-xs text-zinc-600">
                        <summary className="cursor-pointer select-none">
                          引用（{m.refs.length}）
                        </summary>
                        <pre className="mt-2 overflow-auto rounded bg-zinc-50 p-2">
                          {JSON.stringify(m.refs, null, 2)}
                        </pre>
                      </details>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-zinc-100 p-3">
            <div className="flex gap-2">
              <Input
                placeholder="开始输入…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void onSend();
                  }
                }}
              />
              <Button onClick={onSend} disabled={!input.trim() || sending}>
                {sending ? "发送中…" : "发送"}
              </Button>
            </div>
          </div>
        </main>
      </div>

      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              {previewData?.title || "文档预览"}
            </DialogTitle>
          </DialogHeader>
          {previewLoading ? (
            <div className="text-sm text-zinc-500">加载中…</div>
          ) : previewError ? (
            <div className="text-sm text-red-600">{previewError}</div>
          ) : previewData ? (
            <div className="space-y-2">
              <div className="text-xs text-zinc-500">
                {previewData.mime_type || "unknown"}
                {previewData.file_ext ? ` · .${previewData.file_ext}` : ""}
                {previewData.truncated ? " · 已截断" : ""}
              </div>
              <pre className="max-h-[60vh] overflow-auto rounded border border-zinc-200 bg-zinc-50 p-3 text-xs leading-relaxed">
                {previewData.content}
              </pre>
              <div className="text-xs text-zinc-500">
                下载原文件：
                <a
                  className="ml-1 underline"
                  href={`${apiBaseUrl()}/workspaces/${workspaceId}/documents/${previewData.document_id}/download`}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开
                </a>
              </div>
            </div>
          ) : (
            <div className="text-sm text-zinc-500">暂无内容</div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
