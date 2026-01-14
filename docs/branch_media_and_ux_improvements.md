# 改进分支：媒体能力 + 体验闭环（拟 v0.2）

> 目的：把本文件当作“分支”使用，承载本轮改进的设计与 TODO 清单。
> 当本文件中的 TODO 全部完成后，再将结论与最终方案合并回 `docs/architecture.md`，并同步更新关联文档（例如 `docs/document_type_parsing.md`）。
>
> 约束：如涉及数据库结构变更，必须通过 Alembic 迁移（`alembic/versions/*`）落地，并在本文件记录迁移点与回滚策略。

## 背景

当前 MVP 已跑通“上传 → 异步索引 → Qdrant 召回 → RAG 对话”。但在产品闭环与多媒体能力上仍缺关键能力：

- 对话上下文未做 Redis 缓存（仅有会话锁），导致性能/成本/体验欠佳。
- 历史对话缺“拉取展示 + 引用持久化”闭环。
- 文档只有纯文本 preview；前端缺 download；PDF/DOCX 预览不美观。
- 缺少文件夹批量上传与文件夹形态展示。
- 图片不支持：既要支持 OCR（图片转文本可被 RAG 引用），也要支持多模态理解/检索（图片向量）。

## 目标（本分支完成标准）

1) **对话闭环**：支持历史消息加载、引用可回放；同 conversation 串行（Redis lock）不变。  
2) **缓存闭环**：Redis 缓存最近消息与摘要，cache miss 回源 Postgres。  
3) **文件闭环**：前端具备 download；PDF/DOCX 有富预览；预览与下载对齐同一份原始文件。  
4) **文件夹闭环**：支持文件夹批量上传（保留相对路径），并在 UI 展示树形结构。  
5) **图片双通道索引**：OCR 文本索引 + 多模态图片向量索引同时支持；图片可预览、可下载、可被索引结果引用。

## 非目标（本分支不做/可后续）

- GraphRAG/Neo4j、query rewrite、rerank、混合检索等高级检索推理（见 `docs/architecture.md` 非 MVP）。
- 完整可观测性体系（metrics/tracing/告警）只保留必要日志与错误回写。
- 精准“引用定位到 PDF 页码/坐标、DOCX 段落坐标”的完美回链（本分支先实现可用的近似定位/搜索策略）。

## 设计原则

- **Postgres 是 SoT**：messages / chunks / tasks / docs 全量可追溯；Redis 只做缓存与锁。
- **Qdrant 负责召回**：对话 RAG 默认走“文本向量”召回；图片“多模态”召回作为并行能力提供。
- **保持 workspace contract**：仍保持“每 workspace 一个 collection”的大方向；如需要同时存文本向量与图片向量，优先使用 Qdrant 的 *named vectors*（同 collection 内多向量字段）而非拆多 collection。
- **演进可控**：新增能力尽量以可选配置开关启用；对旧数据可平滑迁移/重建索引。

## 关键契约（API/缓存/索引）

### API（拟补齐/调整）

- 历史对话：
  - `GET /conversations/{conversation_id}/messages?limit=50&before=<cursor>`：分页返回 messages（含 role/content/created_at + refs/metadata）。
- 文件夹批量上传：
  - `POST /workspaces/{workspace_id}/documents/upload_many`：multipart 批量上传，要求每个文件带 `relative_path`（用于 `external_key` 与落盘路径）。
- 图片能力（与 documents 统一）：
  - 上传：复用 upload_many/upload 单文件上传（允许 png/jpg/jpeg/webp 等）。
  - 预览：若为图片，preview 可返回图片 URL/或直接用 download URL 预览。
- 图片检索（可选，若要单独入口）：
  - `POST /workspaces/{workspace_id}/images/search`：以文本或图片输入走多模态向量召回（不影响 chat 默认行为）。

> 说明：download 端点后端已存在（`/documents/{document_id}/download`），本分支重点是前端对接与富预览。

### Redis 缓存键约定（建议）

- 会话锁（已存在）：`lock:conversation:{conversation_id}`（SET NX EX）
- 最近消息缓存：
  - `convo:{conversation_id}:messages`（List，元素为 JSON，LPUSH + LTRIM，建议保留 50）
- 摘要缓存：
  - `convo:{conversation_id}:summary`（String，缓存 `memory_summaries.summary`）

缓存策略：写消息时同步更新缓存；读取时 cache miss 回源 DB 并回填。

### Qdrant（named vectors 方案）

在同一个 workspace collection 中存两类向量：

- `text`：文本 embedding（用于 chat/RAG 默认召回）
- `image`：图片 embedding（用于以图搜图/以文搜图等多模态能力）

数据写入规则：
- 文本类文档 chunk：只写 `text` 向量
- 图片类文档 chunk：
  - OCR 产出的文本 chunk：写 `text` 向量（可被 RAG 引用）
  - 图片本体向量：写 `image` 向量（用于多模态召回）

payload 最少字段（沿用既有 contract）：
- `chunk_uid`
- `document_id`
- `document_version_id`
- `offset_start/offset_end`（对 OCR 文本 chunk 有意义；对图片本体可设为 0..0 或 null）
- `modality`：`text|image`（建议新增，用于过滤/展示）

> 迁移策略：已存在的 collection 为单向量；如升级为 named vectors，需要：
> 1) 新建 collection（或重建）以适配新 vectors schema；或
> 2) 保持旧 collection，创建新 workspace 时使用新 schema（需要版本化处理）。
> 本分支优先选择“可控重建”：提供 reindex 任务，避免线上静默破坏。

## 数据库变更（如做则必须迁移）

### 必做（历史对话引用回放）

二选一（建议先 A，后续可演进到 B）：

A) `messages` 增加 `metadata jsonb not null default '{}'`
- assistant 消息把 `refs` 原样存入 `metadata.refs`

B) 新表 `message_refs`
- `message_id`（fk messages.id）
- `chunk_uid`
- `title_path`
- `offset_start/offset_end`
- 以及必要索引（`(message_id)`，`(chunk_uid)`）

### 可选（文件夹展示/过滤性能）

- `documents.external_key` 增加适配前缀查询的索引（例如 `btree` + pattern ops 或 `pg_trgm` gin），以支持按路径前缀列文件夹。

### 可选（图片/多模态治理）

- 若需要区分“图片本体向量”与“OCR 文本 chunk”在 UI 展示层的关联，可新增字段或表做映射（例如 `chunks.source_kind` / `chunk_meta`）。
  - MVP 可先把 `modality` 放入 Qdrant payload + chunk.title_path 约定（例如 `["image", filename]`）解决展示。

## 索引流水线（task）扩展

沿用单 `document_index` task 的主结构，但内部逻辑扩展为：

- 文本类：parse→chunk→text embedding upsert→delete_old
- 图片类（双通道）：
  1) OCR 抽取文本（得到可引用的证据文本）
  2) 生成 OCR 文本 chunks → `text` embedding upsert
  3) 生成图片向量 → `image` embedding upsert（同 collection 的 named vectors）
  4) delete_old（按 document_version 清理旧 points）

进度/阶段：
- stage 字段可继续复用 `chunk/embedding_upsert`，但内部应记录更细 result（例如 `result.ocr_chunks/indexed_text/indexed_image`）。
- 如 UI 需要更细阶段，可扩展 `stage` 枚举（会影响文档与前端展示，但不影响 DB schema；仍需约定）。

## 前端（体验闭环）

- Download：文件列表每行提供下载按钮（直连后端 download）。
- 富预览：
  - PDF：pdf.js
  - DOCX：docx-preview 或 mammoth 转 HTML
  - 图片：img 标签预览
- 文件夹：
  - 上传：支持目录选择（webkitdirectory）+ 批量进度提示
  - 展示：树形结构（由 external_key 聚合）
- 对话：
  - 加载最近 conversation + 历史 messages
  - assistant 消息展示引用 refs，并支持点击打开文档预览（MVP 可先用“在预览中搜索 chunk 文本”代替精确 offset 定位）

---

# TODO 清单（完成后合并回架构文档）

## P0：对话历史 + 引用回放

- [x] 后端：实现 `GET /conversations/{conversation_id}/messages`（分页/游标）
- [x] DB：采用 `messages.metadata jsonb` 并做 Alembic 迁移（`0003_add_message_metadata`）
- [x] 后端：assistant 回复时持久化 refs（写入 `messages.metadata.refs`）
- [x] 前端：进入 workspace 后按 `current_conversation:<workspaceId>` 加载历史消息；支持“加载更多历史”

验收：
- 刷新页面后，历史对话可完整回放（至少最近 50 条），且引用可展示。

## P0：Redis 缓存最近消息 + 摘要

- [x] 约定并实现 Redis keys（messages list + summary string）
- [x] 写消息时更新缓存；读历史时优先走缓存，miss 回源 DB
- [x] 保持现有会话锁逻辑不变

验收：
- 热对话读取不再每次都全量查 DB；锁冲突仍返回 409。

## P0：前端下载

- [x] 前端文件树每个文件提供下载链接，使用 `GET /documents/{document_id}/download`

验收：
- 任意文档可一键下载原文件。

## P1：PDF/DOCX 富预览

- [x] PDF：使用浏览器原生预览（iframe 打开 download URL）
- [x] DOCX：使用 `docx-preview` 渲染
- [x] 引用点击：MVP 先展示 refs（JSON）；后续可把“点击引用→打开文档并搜索”做成增强

验收：
- PDF/DOCX 不再是纯文本预览，体验可用。

## P1：文件夹批量上传 + 树形展示

- [x] 前端：目录选择上传（`webkitdirectory`，保留 `webkitRelativePath`）
- [x] 后端：`POST /workspaces/{workspace_id}/documents/upload_many`（防目录穿越；`external_key=relative_path`）
- [x] 后端：`GET /workspaces/{workspace_id}/documents/tree` 返回树结构（可选 `prefix`）
- [x] 前端：左侧树形文件夹展示

验收：
- 上传一个文件夹后，UI 可按文件夹结构浏览并预览/下载其中任意文件。

## P2：图片双通道（OCR + 多模态）

- [x] 更新 `docs/document_type_parsing.md`：图片类型支持与策略（OCR + multimodal）
- [x] 支持图片上传（扩展名/落盘/下载/预览）
- [x] OCR：抽取文本并作为证据 chunk 写入 DB + `text` 向量索引（图片 preview 默认展示图片本体）
- [x] 多模态 embedding：生成图片向量并写入 Qdrant `image` named vector（需启用 `multimodal.enabled`）
- [x] 检索：
  - [x] chat 默认仍走 `text` 召回（可命中 OCR 证据）
  - [x] 新增图片检索接口：`POST /workspaces/{workspace_id}/images/search`（走 `image` 向量召回）

---

## 实现落地点（便于回溯）

- DB 迁移：`alembic/versions/0003_add_message_metadata.py`
- 历史消息 API：`document_graph/api/routers/messages.py`
- Redis 缓存：`document_graph/redis_utils.py` + `document_graph/langgraph/chat_flow.py`
- 文件夹批量上传与树：`document_graph/api/routers/documents.py`
- 图片 OCR：`document_graph/document_parsing.py`（pytesseract + tesseract）
- 多模态 embedding：`document_graph/multimodal.py`（open_clip_torch）
- Qdrant named vectors：`document_graph/vectorstore/qdrant_index.py` + `document_graph/tasks/document_index.py`
- 图片检索 API：`document_graph/api/routers/images.py`
- 前端：`frontend/src/app/w/[workspaceId]/page.tsx` + `frontend/src/lib/api.ts`

## 启用说明（多模态）

- OCR：默认启用（见 `config.example.yaml` 的 `ocr.*`）。容器镜像已安装 tesseract（见 `Dockerfile`）。
- 多模态：默认关闭。启用需在 `config.yaml` 设置 `multimodal.enabled: true`。
  - 注意：启用多模态会要求 workspace 对应的 Qdrant collection 使用 named vectors（`text` + `image`）。已存在的旧 collection（单向量）将不兼容，建议新 workspace 或重建索引。

验收：
- 上传图片后：能在对话中基于 OCR 文本命中并引用；也能通过多模态检索入口命中图片。
