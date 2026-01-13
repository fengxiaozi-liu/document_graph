# 前端 UI（NotebookLLM 风格，MVP）

本文件定义 document_graph 的前端 MVP 页面与技术方案。目标是实现“workspace 列表页 + workspace 详情页”的最小闭环，并对接 FastAPI 后端。

## 技术选型（MVP）

- 框架：Next.js（React + TypeScript）
- 样式：TailwindCSS
- 组件库：shadcn/ui（Dialog / Dropdown / Card / Input / Button / Tabs）
- 数据请求：fetch（MVP）或 React Query（后续增强缓存/轮询）
- 部署：前端独立容器（后续加入 compose），通过 `NEXT_PUBLIC_API_BASE_URL` 指向后端

## 页面范围

### 1) 首页：Workspace 列表页（参考图 1）

需求（已确认）：
- 默认展示“全部 workspace”的前 5 个（可提供“查看更多”进入完整列表或分页/无限滚动，MVP 可先不做）
- 提供搜索框：按 workspace `name` 搜索匹配并展示结果
- 维护“最近打开的 workspace”：使用 localStorage 记录 `{workspace_id, last_opened_at}`，页面展示前 N 条
- 卡片封面：随机生成或固定样式（建议：固定渐变 + 由 workspace_id 派生稳定颜色，避免每次刷新变化）

交互（MVP）：
- 点击卡片进入详情页 `/w/{workspace_id}`
- “新建”按钮：弹窗输入 workspace 名称（可选 alias），创建后跳转详情页

### 2) Workspace 详情页（参考图 2，Studio 暂不做）

布局（MVP）：
- 左侧：文件列表（来源=上传文件粒度）
  - 默认分页展示前 10 个文件
  - “上传文件”按钮：触发上传并创建索引 task
  - 展示每个文件的索引状态（pending/running/succeeded/failed）与错误（若有）
- 中间：对话区（conversation）
  - 消息列表 + 输入框
  - assistant 回复下方展示引用 refs（chunk_uid/offset/title_path 等）
- 右侧：Studio 面板不渲染（保留占位空间可选）

约束（已确认）：
- 不提供“资料勾选/选择来源”；因为 embedding 库按 workspace 对应 collection 检索即可。

## 路由与状态管理（建议）

- 路由：
  - `/`：workspace 列表页
  - `/w/[workspaceId]`：workspace 详情页
- 本地状态：
  - `recent_workspaces`：localStorage 保存最近打开列表
  - `current_conversation:<workspaceId>`：localStorage 记录该 workspace 最近一次 `conversation_id`（便于恢复会话）

## 后端接口契约（MVP 对接清单）

已存在：
- `POST /workspaces`：创建 workspace
- `GET /workspaces`：列出 workspace
- `GET /workspaces/{workspace_id}`：workspace 详情
- `DELETE /workspaces/{workspace_id}`：删除 workspace（连带删除 Qdrant collection + alias）
- `POST /workspaces/{workspace_id}/documents/upload`：上传文件并触发索引任务（返回 `task_id`）
- `POST /workspaces/{workspace_id}/chat`：对话问答（支持传 `conversation_id` 续聊）
- `POST /workspaces/{workspace_id}/conversations` / `GET /workspaces/{workspace_id}/conversations`：新建/列出对话框

建议补齐（用于 UI 完整闭环）：
- `GET /tasks/{task_id}`：查询索引任务状态（用于轮询展示进度/错误）
- `GET /workspaces/{workspace_id}/documents?limit=10&cursor=...`：分页列出文件/文档及其最新版本/索引状态

## MVP 之后的演进（不在本次范围）

- 完整 workspace 列表分页/排序/筛选（最近/创建时间/来源数）
- 文件列表搜索、批量操作、删除文件与回收策略
- 引用定位到原文预览（按 storage_uri + offset 定位）
- WebSocket/SSE 推送 task 状态（替代轮询）
- Studio 面板与“笔记/报告”等产物

