# 数据模型（Postgres + Qdrant Contract）

本文件描述 document_graph 下一版（workspace + 多轮对话 + 异步索引）的最小数据结构，用于落库与 API 设计对齐。

## 设计原则

- **Postgres 是真相来源（SoT）**：workspace / 文档版本 / chunks / task / 对话消息都落库，可追溯可重放。
- **Qdrant 只做向量召回**：每个 workspace 一个 collection；payload 仅存检索与过滤必要字段，chunk 正文与引用元数据从 Postgres 补全。
- **文件级差分**：文件内容变化即该文档全量重建 chunks + embeddings。
- **任务粒度（MVP）**：每次上传/更新文档仅创建一个“文档索引任务”，内部按 stage 串行执行。

## 表结构（MVP）

### `workspaces`

用途：工作空间本体、与 Qdrant collection/alias 的绑定。

- `id` uuid pk
- `name` text not null  （展示名，可中文）
- `qdrant_collection` text not null unique （英文，建议 `ws_<uuid>`）
- `qdrant_alias` text unique null （可中文，用于展示；可选）
- `created_at` timestamptz not null default now()
- `updated_at` timestamptz not null default now()

索引/约束：
- `unique(qdrant_collection)`
- `unique(qdrant_alias)`（若启用）

### `sources`

用途：workspace 下的“来源/namespace”（本地上传、URL、飞书等；MVP 先做本地上传）。

- `id` uuid pk
- `workspace_id` uuid not null fk -> `workspaces.id`
- `type` text not null  （MVP：`local_upload`）
- `name` text not null  （展示名）
- `config` jsonb not null default '{}'  （例如根路径/bucket 等；MVP 可空）
- `created_at` timestamptz not null default now()
- `updated_at` timestamptz not null default now()

索引/约束：
- index `(workspace_id)`

### `documents`

用途：文档“逻辑实体”，不承载版本细节。

- `id` uuid pk
- `workspace_id` uuid not null fk -> `workspaces.id`
- `source_id` uuid not null fk -> `sources.id`
- `external_key` text not null  （来源侧稳定标识；本地上传可用相对路径或上传生成 key）
- `title` text not null
- `status` text not null default 'active'  （`active|error`，可扩展）
- `created_at` timestamptz not null default now()
- `updated_at` timestamptz not null default now()

索引/约束：
- `unique(workspace_id, source_id, external_key)`
- index `(workspace_id)`
- index `(source_id)`

### `document_versions`

用途：文件级差分与版本治理的基础。每次内容变化新增一条版本记录。

- `id` uuid pk
- `document_id` uuid not null fk -> `documents.id`
- `version` bigint not null  （从 1 递增）
- `content_hash` text not null  （sha256/sha1 均可，建议 sha256）
- `size_bytes` bigint not null
- `mtime` timestamptz null  （能拿到则写）
- `storage_uri` text not null  （本地路径或 s3 uri）
- `mime_type` text null  （用于解析器选择与类型识别）
- `file_ext` text null  （如 `md/txt/pdf`，用于解析器选择与展示）
- `created_at` timestamptz not null default now()

索引/约束：
- `unique(document_id, version)`
- `unique(document_id, content_hash)`（可选，避免重复版本）
- index `(document_id, created_at desc)`
解析策略与类型处理详见 `docs/document_type_parsing.md`。

### `chunks`

用途：回答与引用回链所需的“chunk 正文 + 元数据”落库。

- `id` uuid pk
- `document_version_id` uuid not null fk -> `document_versions.id`
- `chunk_index` int not null
- `chunk_uid` text not null unique  （建议：`chunk_<document_id>_<version>_<chunk_index>` 或等价稳定格式）
- `title_path` jsonb not null default '[]'  （数组）
- `offset_start` int not null
- `offset_end` int not null
- `text` text not null  （MVP 建议存；后续可外置或压缩）
- `text_hash` text not null  （用于排错与一致性校验）
- `created_at` timestamptz not null default now()

索引/约束：
- `unique(chunk_uid)`
- `unique(document_version_id, chunk_index)`
- index `(document_version_id)`

### `tasks`

用途：异步任务（Celery + Redis）状态回写，供 UI 展示/排错/重试。

- `id` uuid pk
- `workspace_id` uuid not null fk -> `workspaces.id`
- `document_id` uuid null fk -> `documents.id`  （索引任务通常绑定文档；其他任务可为空）
- `type` text not null  （MVP：`document_index`）
- `status` text not null  （`pending|running|succeeded|failed|canceled`）
- `stage` text null  （`persist_meta|chunk|embedding_upsert|delete_old`）
- `progress` real null  （0..1）
- `idempotency_key` text not null unique  （建议：`workspace_id:document_id:content_hash:type`）
- `celery_task_id` text unique null
- `input` jsonb not null default '{}'  （入参快照）
- `result` jsonb not null default '{}'  （产出摘要，例如 indexed_chunks 数量）
- `error` jsonb not null default '{}'  （错误结构：code/message/stack/extra）
- `attempt` int not null default 0
- `max_attempts` int not null default 3
- `created_at` timestamptz not null default now()
- `started_at` timestamptz null
- `finished_at` timestamptz null
- `updated_at` timestamptz not null default now()

索引/约束：
- `unique(idempotency_key)`
- index `(workspace_id, created_at desc)`
- index `(document_id, created_at desc)`
- index `(status, updated_at desc)`

### `conversations`

用途：workspace 内的对话会话。

- `id` uuid pk
- `workspace_id` uuid not null fk -> `workspaces.id`
- `title` text not null default ''  （可由首问生成）
- `created_at` timestamptz not null default now()
- `updated_at` timestamptz not null default now()

索引/约束：
- index `(workspace_id, updated_at desc)`

### `messages`

用途：对话消息记录（Postgres 为 SoT）。

- `id` uuid pk
- `conversation_id` uuid not null fk -> `conversations.id`
- `role` text not null  （`user|assistant|system|tool`）
- `content` text not null
- `created_at` timestamptz not null default now()

索引/约束：
- index `(conversation_id, created_at asc)`

### `memory_summaries`（可选但推荐）

用途：长对话摘要（窗口压缩）；Redis 可缓存该摘要，缓存失效可回源 Postgres。

- `conversation_id` uuid pk fk -> `conversations.id`
- `summary` text not null default ''
- `updated_at` timestamptz not null default now()
- `last_message_id` uuid null fk -> `messages.id`  （标记摘要覆盖到哪条消息）

## Qdrant Contract（每 workspace 一个 collection）

### Collection 命名

- collection：英文（建议 `ws_<uuid>`），由 `workspaces.qdrant_collection` 管理
- alias：可中文，由 `workspaces.qdrant_alias` 管理（用于 UI 展示）

### Payload（最小字段）

建议 payload 最少包含：
- `chunk_uid`（用于回查 Postgres `chunks`）
- `document_id`
- `document_version_id` 或 `version`
- `offset_start` / `offset_end`（可选，但有助于排错）
- `source_uri`（可选；若不放 Qdrant，则从 Postgres 补全）

### 查询链路

1) embed(query) → Qdrant 搜索（workspace collection）召回 `top-k`，拿到 `chunk_uid` 列表  
2) 用 `chunk_uid` 批量查询 Postgres `chunks` 补全 `text + 引用元数据`  
3) 组装 prompt → LLM 生成答案与引用

## Task（MVP）执行阶段与写表

`document_index` task（单任务，串行阶段）：

- `persist_meta`：upsert `documents`；insert `document_versions`
- `chunk`：insert `chunks`
- `embedding_upsert`：upsert Qdrant points（payload 绑定 `chunk_uid`）
- `delete_old`：Qdrant filter delete（按 `document_id + previous_version`/`document_version_id`）

