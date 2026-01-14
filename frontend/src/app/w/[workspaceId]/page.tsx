"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { renderAsync } from "docx-preview";
import { File, FileImage, FileText, Folder, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { PdfViewer } from "@/components/pdf-viewer";
import {
  apiBaseUrl,
  chat,
  deleteByPrefix,
  deleteDocument,
  deleteMany,
  getDocumentPreview,
  getDocumentTree,
  getTask,
  listConversationMessages,
  uploadDocument,
  uploadMany,
  type DocumentPreview,
  type DocumentTreeNode,
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

function nodeKey(node: DocumentTreeNode) {
  if (node.type === "folder") return node.path || "__root__";
  return node.document?.id || node.path;
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ workspaceId: string }>();
  const workspaceId = params.workspaceId;

  const [tree, setTree] = useState<DocumentTreeNode | null>(null);
  const [docLoading, setDocLoading] = useState(true);
  const [docError, setDocError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    __root__: true,
  });

  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const folderRef = useRef<HTMLInputElement | null>(null);
  const [deleteMode, setDeleteMode] = useState(false);
  const [selectedDocIds, setSelectedDocIds] = useState<Record<string, boolean>>(
    {},
  );

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

  const [messages, setMessages] = useState<
    Array<{ role: "user" | "assistant"; content: string; refs?: RefItem[] }>
  >([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendStage, setSendStage] = useState<string | null>(null);

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [historyNextBefore, setHistoryNextBefore] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<DocumentPreview | null>(null);
  const docxContainerRef = useRef<HTMLDivElement | null>(null);

  async function refreshTree() {
    setDocLoading(true);
    setDocError(null);
    try {
      const root = await getDocumentTree(workspaceId);
      setTree(root);
    } catch (e: unknown) {
      setDocError(errToString(e));
    } finally {
      setDocLoading(false);
    }
  }

  useEffect(() => {
    refreshTree();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  useEffect(() => {
    try {
      const v = window.localStorage.getItem(conversationKey(workspaceId));
      if (v) setConversationId(v);
    } catch {
      // ignore
    }
  }, [workspaceId]);

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
            return { docId, latest };
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
          if (
            status === "succeeded" ||
            status === "failed" ||
            status === "canceled"
          ) {
            delete next[u.docId];
            shouldRefreshDocs = true;
          }
        }
        return next;
      });

      if (shouldRefreshDocs) {
        await refreshTree();
      }
    }, 1500);

    return () => window.clearInterval(timer);
  }, [activeIndexTasks, workspaceId]);

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
      await refreshTree();
    } finally {
      setUploading(false);
    }
  }

  async function onUploadFolder(files: File[]) {
    setUploading(true);
    try {
      const relativePaths = files.map(
        (f) => (f as any).webkitRelativePath || f.name,
      );
      const res = await uploadMany(workspaceId, files, relativePaths);
      setActiveIndexTasks((prev) => {
        const next = { ...prev };
        for (const it of res.items) {
          next[it.document_id] = {
            taskId: it.task_id,
            status: it.task_status,
            stage: null,
            progress: null,
          };
        }
        return next;
      });
      await refreshTree();
    } finally {
      setUploading(false);
    }
  }

  function clearSelection() {
    setSelectedDocIds({});
  }

  function selectionStats(ids: string[]) {
    if (ids.length === 0) return { any: false, all: false };
    const any = ids.some((id) => Boolean(selectedDocIds[id]));
    const all = ids.every((id) => Boolean(selectedDocIds[id]));
    return { any, all };
  }

  function toggleSelectedDoc(id: string, checked: boolean) {
    setSelectedDocIds((prev) => {
      const next = { ...prev };
      if (checked) next[id] = true;
      else delete next[id];
      return next;
    });
  }

  function collectDocIds(node: DocumentTreeNode): string[] {
    if (node.type === "file") {
      const id = node.document?.id;
      return id ? [id] : [];
    }
    const out: string[] = [];
    for (const c of node.children || []) out.push(...collectDocIds(c));
    return out;
  }

  function toggleFolderSelection(node: DocumentTreeNode) {
    const ids = collectDocIds(node);
    const { all } = selectionStats(ids);
    setSelectedDocIds((prev) => {
      const next = { ...prev };
      for (const id of ids) {
        if (all) delete next[id];
        else next[id] = true;
      }
      return next;
    });
  }

  async function onDeleteSelected() {
    const ids = Object.keys(selectedDocIds);
    if (ids.length === 0) return;
    if (!window.confirm(`确认删除选中的 ${ids.length} 个文件？此操作将同时删除向量与 chunks。`)) {
      return;
    }
    try {
      await deleteMany(workspaceId, { document_ids: ids });
      clearSelection();
      setDeleteMode(false);
      await refreshTree();
    } catch (e: unknown) {
      alert(`删除失败：${errToString(e)}`);
    }
  }

  async function onDeleteFile(docId: string, title: string) {
    if (!window.confirm(`确认删除文件：${title}？`)) return;
    try {
      await deleteDocument(workspaceId, docId);
      await refreshTree();
    } catch (e: unknown) {
      alert(`删除失败：${errToString(e)}`);
    }
  }

  async function onDeleteFolder(prefix: string) {
    if (!window.confirm(`确认删除文件夹：${prefix}？将删除其下所有文件与索引。`)) return;
    try {
      await deleteByPrefix(workspaceId, prefix);
      await refreshTree();
    } catch (e: unknown) {
      alert(`删除失败：${errToString(e)}`);
    }
  }

  async function loadHistory(opts?: { before?: string | null }) {
    if (!conversationId) return;
    setHistoryLoading(true);
    try {
      const resp = await listConversationMessages(conversationId, {
        limit: 50,
        before: opts?.before ?? null,
      });
      const mapped = resp.items
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
          refs: (m.metadata?.["refs"] as RefItem[] | undefined) || undefined,
        }));
      setMessages((prev) => (opts?.before ? [...mapped, ...prev] : mapped));
      setHistoryNextBefore(resp.next_before ?? null);
    } catch (e: unknown) {
      console.warn("load_history_failed", e);
    } finally {
      setHistoryLoading(false);
    }
  }

  useEffect(() => {
    if (!conversationId) {
      setHistoryNextBefore(null);
      return;
    }
    void loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  async function onPreview(documentId: string) {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewData(null);
    try {
      const data = await getDocumentPreview(workspaceId, documentId);
      setPreviewData(data);
    } catch (e: unknown) {
      setPreviewError(errToString(e));
    } finally {
      setPreviewLoading(false);
    }
  }

  useEffect(() => {
    if (!previewOpen) return;
    if (!previewData || previewData.file_ext !== "docx") return;
    const viewUrl = previewData.view_url;
    let cancelled = false;

    async function run() {
      try {
        const url = `${apiBaseUrl()}${viewUrl}`;
        const resp = await fetch(url);
        const buf = await resp.arrayBuffer();
        if (cancelled) return;
        const container = docxContainerRef.current;
        if (!container) return;
        container.innerHTML = "";
        await renderAsync(buf, container, undefined, {
          className: "docx",
          inWrapper: true,
        });
      } catch (e: unknown) {
        if (!cancelled) setPreviewError(errToString(e));
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [previewOpen, previewData]);

  async function onSend() {
    const text = input.trim();
    if (!text) return;
    setInput("");
    setSending(true);
    setSendStage("正在检索相关片段…");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    // Placeholder assistant message while we wait.
    setMessages((prev) => [...prev, { role: "assistant", content: "__pending__" }]);
    try {
      window.setTimeout(() => {
        setSendStage((cur) => (cur ? "正在生成回答…" : cur));
      }, 900);
      const out = await chat(workspaceId, {
        conversation_id: conversationId,
        message: text,
      });
      setConversationId(out.conversation_id);
      window.localStorage.setItem(
        conversationKey(workspaceId),
        out.conversation_id,
      );
      setMessages((prev) => {
        const next = [...prev];
        // Replace last pending assistant message if present.
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === "assistant" && next[i].content === "__pending__") {
            next[i] = {
              role: "assistant",
              content: String(out.answer || ""),
              refs: (out.refs as RefItem[]) || [],
            };
            return next;
          }
        }
        next.push({
          role: "assistant",
          content: String(out.answer || ""),
          refs: (out.refs as RefItem[]) || [],
        });
        return next;
      });
    } catch (e: unknown) {
      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === "assistant" && next[i].content === "__pending__") {
            next[i] = { role: "assistant", content: `请求失败：${errToString(e)}` };
            return next;
          }
        }
        next.push({ role: "assistant", content: `请求失败：${errToString(e)}` });
        return next;
      });
    } finally {
      setSending(false);
      setSendStage(null);
    }
  }

  function toggleFolder(path: string) {
    setExpanded((prev) => ({ ...prev, [path]: !prev[path] }));
  }

  function fileIcon(name: string) {
    const lower = name.toLowerCase();
    if (lower.endsWith(".pdf") || lower.endsWith(".docx") || lower.endsWith(".md") || lower.endsWith(".txt")) {
      return <FileText className="h-4 w-4 text-zinc-500" />;
    }
    if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".webp")) {
      return <FileImage className="h-4 w-4 text-zinc-500" />;
    }
    return <File className="h-4 w-4 text-zinc-500" />;
  }

  function renderNode(node: DocumentTreeNode, depth: number) {
    if (depth === 0 && node.type === "folder" && !node.name && !node.path) {
      return (
        <div key="__root__" className="space-y-1">
          {(node.children || []).map((c) => renderNode(c, 1))}
        </div>
      );
    }

    if (node.type === "folder") {
      const key = nodeKey(node);
      const isOpen = expanded[key] ?? depth === 0;
      const folderIds = deleteMode ? collectDocIds(node) : [];
      const folderSel = deleteMode ? selectionStats(folderIds) : { any: false, all: false };
      return (
        <div key={key}>
          <div
            role="button"
            tabIndex={0}
            className="flex w-full cursor-pointer items-center justify-between gap-2 rounded px-2 py-2 text-left text-sm hover:bg-zinc-50"
            onClick={() => toggleFolder(key)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                toggleFolder(key);
              }
            }}
          >
            <div className="flex min-w-0 items-center gap-2">
              {deleteMode ? (
                <input
                  ref={(el) => {
                    if (!el) return;
                    el.indeterminate = folderSel.any && !folderSel.all;
                  }}
                  type="checkbox"
                  checked={folderSel.all}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => {
                    e.stopPropagation();
                    toggleFolderSelection(node);
                  }}
                  className="h-4 w-4 shrink-0 accent-red-600"
                  aria-label="选择文件夹"
                />
              ) : null}
              <span className="w-4 shrink-0 text-xs text-zinc-500">
                {isOpen ? "▾" : "▸"}
              </span>
              <Folder className="h-4 w-4 shrink-0 text-amber-600" />
              <span className="truncate font-medium">{node.name || "文件夹"}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-zinc-400">
                {(node.children || []).length}
              </span>
              {node.path ? (
                <button
                  type="button"
                  className="rounded border border-zinc-200 bg-white px-2 py-1 text-[10px] text-zinc-600 hover:bg-zinc-50"
                  onClick={(e) => {
                    e.stopPropagation();
                    void onDeleteFolder(node.path);
                  }}
                >
                  删除
                </button>
              ) : null}
              {deleteMode ? (
                <button
                  type="button"
                  className="rounded border border-zinc-200 bg-white px-2 py-1 text-[10px] text-zinc-600 hover:bg-zinc-50"
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleFolderSelection(node);
                  }}
                >
                  {(() => {
                    return folderSel.all ? "取消选中" : "全选";
                  })()}
                </button>
              ) : null}
            </div>
          </div>
          {isOpen ? (
            <div className="ml-4 border-l border-zinc-100 pl-2">
              {(node.children || []).map((c) => renderNode(c, depth + 1))}
            </div>
          ) : null}
        </div>
      );
    }

    const doc = node.document;
    if (!doc) return <div key={node.path} />;
    const latest = doc.latest_task;
    const active = activeIndexTasks[doc.id];
    const status = active?.status || latest?.status || "—";
    return (
      <div
        key={node.path}
        className="flex items-center justify-between gap-2 rounded px-2 py-2 hover:bg-zinc-50"
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {deleteMode ? (
            <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 truncate text-left text-sm">
              <input
                type="checkbox"
                checked={Boolean(selectedDocIds[doc.id])}
                onChange={(e) => toggleSelectedDoc(doc.id, e.target.checked)}
                className="h-4 w-4 shrink-0 accent-red-600"
              />
              {fileIcon(node.name)}
              <span className="truncate">{node.name}</span>
            </label>
          ) : (
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-2 truncate text-left text-sm"
              onClick={() => void onPreview(doc.id)}
            >
              {fileIcon(node.name)}
              <span className="truncate">{node.name}</span>
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-zinc-500">{status}</span>
          <a
            className="text-[10px] text-blue-600 underline"
            href={`${apiBaseUrl()}/workspaces/${workspaceId}/documents/${doc.id}/download`}
            target="_blank"
            rel="noreferrer"
          >
            下载
          </a>
          {deleteMode ? (
            <button
              type="button"
              className="rounded border border-zinc-200 bg-white px-2 py-1 text-[10px] text-zinc-600 hover:bg-zinc-50"
              onClick={() => void onPreview(doc.id)}
            >
              预览
            </button>
          ) : null}
          <button
            type="button"
            className="rounded border border-zinc-200 bg-white px-2 py-1 text-[10px] text-zinc-600 hover:bg-zinc-50"
            onClick={() => void onDeleteFile(doc.id, node.name)}
          >
            删除
          </button>
        </div>
      </div>
    );
  }

  const treeView = useMemo(() => {
    if (!tree) return null;
    return renderNode(tree, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree, expanded, activeIndexTasks, deleteMode, selectedDocIds]);

  return (
    <div className="min-h-screen bg-zinc-50">
      <div className="mx-auto max-w-6xl px-6 py-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <div className="text-lg font-semibold">Workspace</div>
            <div className="text-xs text-zinc-500">{workspaceId}</div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => refreshTree()}>
              刷新
            </Button>
          </div>
        </div>

        <div className="grid h-[80vh] grid-cols-[360px_1fr] gap-4">
          <aside className="space-y-3 overflow-auto rounded-xl border border-zinc-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold">文档</div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void onUploadFile(f);
                    e.target.value = "";
                  }}
                />
                <input
                  ref={folderRef}
                  type="file"
                  multiple
                  // @ts-expect-error - webkitdirectory is non-standard but widely supported.
                  webkitdirectory="true"
                  className="hidden"
                  onChange={(e) => {
                    const fs = e.target.files ? Array.from(e.target.files) : [];
                    if (fs.length > 0) void onUploadFolder(fs);
                    e.target.value = "";
                  }}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={uploading}
                  onClick={() => fileRef.current?.click()}
                  className="whitespace-nowrap"
                >
                  {uploading ? "上传中…" : "上传文件"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={uploading}
                  onClick={() => folderRef.current?.click()}
                  className="whitespace-nowrap"
                >
                  {uploading ? "上传中…" : "上传文件夹"}
                </Button>
                <Button
                  size="sm"
                  variant={deleteMode ? "secondary" : "outline"}
                  onClick={() => {
                    if (deleteMode) {
                      setDeleteMode(false);
                      clearSelection();
                    } else {
                      setDeleteMode(true);
                      clearSelection();
                    }
                  }}
                  className="whitespace-nowrap"
                >
                  {deleteMode ? "退出删除" : "批量删除"}
                </Button>
                {deleteMode ? (
                  <Button
                    size="sm"
                    disabled={Object.keys(selectedDocIds).length === 0}
                    onClick={() => void onDeleteSelected()}
                    className="whitespace-nowrap"
                  >
                    删除选中
                  </Button>
                ) : null}
              </div>
            </div>

            <Card className="p-2">
              {docLoading ? (
                <div className="p-2 text-sm text-zinc-500">加载中…</div>
              ) : docError ? (
                <div className="p-2 text-sm text-red-600">{docError}</div>
              ) : !tree || (tree.children || []).length === 0 ? (
                <div className="p-2 text-sm text-zinc-500">暂无文档</div>
              ) : (
                <div className="space-y-1">{treeView}</div>
              )}
            </Card>
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
                  setHistoryNextBefore(null);
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
                  {historyNextBefore ? (
                    <div className="flex justify-center">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={historyLoading}
                        onClick={() =>
                          void loadHistory({ before: historyNextBefore })
                        }
                      >
                        {historyLoading ? "加载中…" : "加载更多历史"}
                      </Button>
                    </div>
                  ) : null}

                  {messages.map((m, idx) => (
                    <div key={idx} className="space-y-1">
                      <div className="text-xs font-semibold text-zinc-500">
                        {m.role === "user" ? "你" : "助手"}
                      </div>
                      {m.role === "assistant" && m.content === "__pending__" ? (
                        <div className="flex items-center gap-2 rounded-lg border border-zinc-100 bg-zinc-50 p-3 text-sm text-zinc-700">
                          <Loader2 className="h-4 w-4 animate-spin text-zinc-500" />
                          <span>{sendStage || "处理中…"}</span>
                        </div>
                      ) : (
                        <div className="whitespace-pre-wrap rounded-lg border border-zinc-100 bg-zinc-50 p-3 text-sm">
                          {m.content}
                        </div>
                      )}
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

        <Dialog
          open={previewOpen}
          onOpenChange={(open) => {
            setPreviewOpen(open);
            if (!open) {
              setPreviewData(null);
              setPreviewError(null);
              setPreviewLoading(false);
              if (docxContainerRef.current) docxContainerRef.current.innerHTML = "";
            }
          }}
        >
          <DialogContent className="max-h-[90vh] max-w-6xl overflow-hidden">
            <DialogClose asChild>
              <button
                type="button"
                className="absolute right-4 top-4 inline-flex h-9 w-9 items-center justify-center rounded-md border border-zinc-200 bg-white text-zinc-600 hover:bg-zinc-50"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </DialogClose>
            <DialogHeader>
              <DialogTitle>{previewData?.title || "文档预览"}</DialogTitle>
            </DialogHeader>
            {previewLoading ? (
              <div className="text-sm text-zinc-500">加载中…</div>
            ) : previewError ? (
              <div className="text-sm text-red-600">{previewError}</div>
            ) : previewData ? (
              <div className="space-y-2 overflow-hidden">
                <div className="text-xs text-zinc-500">
                  {previewData.mime_type || "unknown"}
                  {previewData.file_ext ? ` · .${previewData.file_ext}` : ""}
                  {previewData.truncated ? " · 已截断" : ""}
                </div>

                {previewData.content_type === "image" ? (
                  <div className="max-h-[60vh] overflow-auto rounded border border-zinc-200 bg-zinc-50 p-2">
                    <img
                      alt={previewData.title}
                      src={`${apiBaseUrl()}${previewData.view_url}`}
                      className="mx-auto max-h-[56vh] object-contain"
                    />
                  </div>
                ) : previewData.file_ext === "pdf" ? (
                  <PdfViewer
                    url={`${apiBaseUrl()}${previewData.view_url}`}
                    className="rounded border border-zinc-200 bg-white"
                  />
                ) : previewData.file_ext === "docx" ? (
                  <div
                    ref={docxContainerRef}
                    className="h-[60vh] overflow-auto rounded border border-zinc-200 bg-white p-4 text-sm"
                  />
                ) : (
                  <pre className="max-h-[60vh] overflow-auto rounded border border-zinc-200 bg-zinc-50 p-3 text-xs leading-relaxed">
                    {previewData.content}
                  </pre>
                )}

                <div className="text-xs text-zinc-500">
                  下载原文件：
                  <a
                    className="ml-1 underline"
                    href={`${apiBaseUrl()}${previewData.download_url}`}
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
    </div>
  );
}
