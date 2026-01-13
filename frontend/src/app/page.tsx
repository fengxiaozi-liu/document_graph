"use client";

import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { WorkspaceCard } from "@/components/workspace-card";
import { createWorkspace, listWorkspaces, type Workspace } from "@/lib/api";

type RecentWorkspace = { workspace_id: string; last_opened_at: string };

function loadRecent(): RecentWorkspace[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem("recent_workspaces");
    const parsed = raw ? (JSON.parse(raw) as RecentWorkspace[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveRecent(items: RecentWorkspace[]) {
  window.localStorage.setItem(
    "recent_workspaces",
    JSON.stringify(items.slice(0, 20)),
  );
}

export default function HomePage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  const recent = useMemo(() => loadRecent(), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listWorkspaces()
      .then((rows) => {
        if (!cancelled) setWorkspaces(rows);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return workspaces;
    return workspaces.filter((w) => w.name.toLowerCase().includes(q));
  }, [search, workspaces]);

  const top5 = filtered.slice(0, 5);

  const recentWorkspaces = useMemo(() => {
    const byId = new Map(workspaces.map((w) => [w.id, w]));
    return recent
      .map((r) => ({
        ws: byId.get(r.workspace_id),
        last_opened_at: r.last_opened_at,
      }))
      .filter(
        (x): x is { ws: Workspace; last_opened_at: string } => Boolean(x.ws),
      )
      .slice(0, 10);
  }, [recent, workspaces]);

  function onOpenWorkspace(workspaceId: string) {
    const now = new Date().toISOString();
    const current = loadRecent();
    const next = [{ workspace_id: workspaceId, last_opened_at: now }].concat(
      current.filter((x) => x.workspace_id !== workspaceId),
    );
    saveRecent(next);
  }

  async function onCreate() {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      const ws = await createWorkspace({ name });
      setWorkspaces((prev) => [ws, ...prev]);
      setNewName("");
      setCreateOpen(false);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50">
      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">工作空间</h1>
            <p className="text-sm text-zinc-500">NotebookLLM 风格 MVP</p>
          </div>
          <div className="flex items-center gap-2">
            <Input
              placeholder="搜索 workspace…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-64"
            />
            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
              <DialogTrigger asChild>
                <Button>+ 新建</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>新建工作空间</DialogTitle>
                  <DialogDescription>
                    创建后会生成对应的 Qdrant collection。
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-3">
                  <Input
                    placeholder="工作空间名称"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                  />
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="secondary"
                      onClick={() => setCreateOpen(false)}
                    >
                      取消
                    </Button>
                    <Button
                      onClick={onCreate}
                      disabled={creating || !newName.trim()}
                    >
                      {creating ? "创建中…" : "创建"}
                    </Button>
                  </div>
                </div>
              </DialogContent>
            </Dialog>
          </div>
        </div>

        <div className="mt-8">
          <h2 className="text-sm font-semibold text-zinc-700">
            全部（前 5 个）
          </h2>
          {loading ? (
            <div className="mt-4 text-sm text-zinc-500">加载中…</div>
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
              {top5.map((w) => (
                <WorkspaceCard
                  key={w.id}
                  workspace={w}
                  onOpen={onOpenWorkspace}
                />
              ))}
            </div>
          )}
        </div>

        <div className="mt-10">
          <h2 className="text-sm font-semibold text-zinc-700">最近打开</h2>
          {recentWorkspaces.length === 0 ? (
            <div className="mt-4 text-sm text-zinc-500">暂无最近打开记录</div>
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {recentWorkspaces.map(({ ws }) => (
                <WorkspaceCard
                  key={ws.id}
                  workspace={ws}
                  onOpen={onOpenWorkspace}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
