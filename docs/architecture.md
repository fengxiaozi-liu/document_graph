# 架构文档（MVP）

本仓库的 MVP 目标是跑通“本地文档 → 向量检索 → 生成式问答（RAG）”的闭环，并为后续 GraphRAG/知识图谱扩展预留接口。

## 变更管理（分支式推进）

本仓库的架构文档采用“主线 + 分支文档”的方式推进：

- `docs/architecture.md`：主线（已落地/已确认的最小闭环）
- `docs/branch_media_and_ux_improvements.md`：本轮改进分支（媒体能力 + 体验闭环）的设计与 TODO 清单

规则：
- 改进项先写入分支文档并按 TODO 清单逐项实现；
- 分支文档 TODO 全部完成后，再将结论合并回 `docs/architecture.md`；
- 如涉及数据库结构变更，必须通过 Alembic 迁移落地；
- 关联文档需同步更新（本轮至少包括 `docs/document_type_parsing.md`）。

## 现状总结（我们已完成）

- 已切换到“workspace + 后端服务化”的重构版（旧版 `scripts/` 脚本 MVP 已移除）。
- 后端：FastAPI（`document_graph/api/main.py`），多轮对话用 LangGraph（见 `docs/langgraph_flow.md`）。
- 元数据与对话：Postgres（见 `docs/data_model.md` + Alembic migrations）。
- 异步索引：Celery+Redis（见 `docs/task_system.md`）。
- 向量检索：Qdrant（每个 workspace 一个 collection，alias 可中文）。
- 统一启动：`docker-compose.yaml`（Postgres/Redis/Qdrant/API/Worker/Migrate）。

## 组件与职责

- **上传与来源（source）**：上传文件归档到 workspace 存储（`data/workspaces/<workspace_id>/raw/`），并写入 `documents/document_versions`。
- **切分（chunking）**：对 `document_versions.storage_uri` 指向的原文切分，写入 Postgres `chunks`。
- **向量化与索引（embedding + index）**：对 `chunks.text` 向量化并写入 Qdrant（workspace collection）。
- **问答（RAG + 多轮对话）**：LangGraph 编排对话；向量召回来自 Qdrant，chunk 正文与引用信息从 Postgres `chunks` 补全。
- **异步任务（task）**：上传后创建单一 `document_index` task，串行执行 `persist_meta -> chunk -> embedding_upsert -> delete_old`，状态回写 Postgres `tasks`。

## 目录与产物约定

```
data/
  workspaces/
    <workspace_id>/
      raw/                          # 上传原文落盘（MVP），写入 document_versions.storage_uri
  qdrant/                            # Qdrant 持久化目录（Docker volume）
```

## 配置（LLM/Embedding/向量库均可配置）

配置文件：复制 `config.example.yaml` 为 `config.yaml` 并填入自己的 key/模型。

- `llm.*`：用于回答（OpenAI 兼容 `/v1/chat/completions`）
- `embedding.*`：用于向量化（OpenAI 兼容 `/v1/embeddings`）
- `qdrant.*`：向量库连接与 collection 名
- `chunking.*`：chunk 规则参数（当前以字符数近似 token）
- `paths.*`：产物路径

也支持环境变量覆盖（用于部署）：
`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`QDRANT_URL`、`POSTGRES_URL`、`REDIS_URL`。

## 数据流

```mermaid
flowchart LR
  A[input/ 原始文档] --> B[ingest_local.py\n规范化+元数据+manifest]
  B --> C[data/local_export/input + meta]
  C --> D[chunk_local.py\n切分 chunks.jsonl]
  D --> E[chunks.jsonl]
  E --> F[index_qdrant.py\nembedding + upsert]
  F --> G[(Qdrant\n向量库)]
  H[用户问题] --> I[qa.py / web_app.py\nembedding(question)]
  I --> G
  G --> J[top-k 证据 chunks]
  J --> K[LLM\n基于证据回答]
  K --> L[答案 + 引用]
```

## Chunk 策略（MVP）

当前实现是“标题优先 + 内容兜底”的最小版：
- Markdown：按 `#..######` 识别标题并形成 `title_path`
- HTML：先提取文本，再按段落切分（MVP 未做正文抽取/去导航栏）
- TXT：按段落切分

默认参数（可在 `config.yaml` 的 `chunking.*` 调整）：
- `target_chars=1200`、`max_chars=2400`、`overlap_chars=200`

## 部署形态（MVP）

- **必须**：
  - Python：运行 ingest/chunk/index/query 脚本
  - Qdrant：向量库（Docker）
  - 远程 LLM/Embedding：通过 OpenAI 兼容接口调用（例如 Qwen 兼容模式）
- **可选**：
  - Neo4j：解释图/探索（后续）
  - GraphRAG：社区摘要/全局摘要（后续）

## 运行路径（推荐顺序）

1. `cp config.example.yaml config.yaml`：生成配置（填 `llm/embedding` 的 base_url、api_key、model）
2. `docker compose -f docker-compose.yaml up -d --build`：启动 Postgres/Redis/Qdrant/API/Worker，并自动执行迁移
3. 通过 API 使用：
   - 创建 workspace：`POST /workspaces`
   - 上传文档触发索引：`POST /workspaces/{workspace_id}/documents/upload`
   - 对话问答：`POST /workspaces/{workspace_id}/chat`

## LlamaIndex（非 MVP）

当前 MVP 主线为 FastAPI + Celery + LangGraph（会话编排）+ Qdrant（向量召回）+ Postgres（chunks/引用与治理）。
若后续需要引入 LlamaIndex 作为“检索与合成回答”的编排层（例如 rerank、多索引、tree_summarize 总结），再单独评估接入方案与目录结构。

## 后续扩展（路线图）

- **下一阶段（推荐）：引入 LlamaIndex 做索引与查询**
  - 目的：用更标准的“索引/查询编排层”替换当前检索编排，让后续扩展（多检索器融合、路由、评测、缓存）更顺滑。
  - 接入方式：
    - 继续复用现有数据准备层（上传→版本→chunks→向量库）
    - 继续复用向量库：Qdrant（或未来替换为 Milvus/pgvector）
    - 在查询侧用 LlamaIndex 的 Query Engine 统一封装：`retrieve → synthesize → citations`
  - 代码组织建议（后续新增）：
    - 新增 `document_graph/llamaindex_*` 适配层（占位），避免再引入脚本入口。

- **再下一阶段：接入 LangGraph 做多步流程/智能体化**
  - 目的：把“单次问答”升级为“有状态的多步图流程”，例如：
    - 问题分类/路由（FAQ/流程/故障/代码/制度）
    - 多路检索并行（向量 + 关键词 + 社区摘要）
    - 追问补充信息（缺少关键约束时先问用户）
    - 自检与引用校验（答案必须可回链）
  - 接入方式：LangGraph 作为最上层编排，底层仍调用 LlamaIndex 的检索/查询能力。

- **GraphRAG**：
  - 轻量阶段：先用 LlamaIndex 的 `tree_summarize` 提升“证据→总结”的稳定性（已提供 `llamaindex_qa.py`）
  - 完整阶段：在 `chunks` 之上增加：实体/关系抽取、社区聚类、社区摘要（topic summaries），查询时使用“社区摘要 + 证据 chunk”联合回答
- **知识图谱（Neo4j）**：
  - 存储实体/关系/文档的解释图，用于可视化探索、路径解释与运营排错
  - 不建议在总结问答 MVP 阶段作为主检索主存储
- **飞书云空间文件夹同步（采集层扩展）**：
  - 通过飞书开放平台把文档导出为 `md/html`，再落到与 `data/local_export/*` 同构的目录结构
  - 下游 chunk/embedding/问答层无需修改

## 架构图（现状 MVP）

```mermaid
flowchart TB
  subgraph Ingest[数据采集/规范化层]
    A[input/ 原始文档] --> B[ingest_local.py\n生成 doc_id/meta/manifest]
    B --> C[data/local_export/input]
    B --> CM[data/local_export/meta]
    B --> MF[data/local_export/manifest.jsonl]
  end

  subgraph Chunk[切分层]
    C --> D[chunk_local.py\n标题优先+内容兜底]
    D --> E[data/local_export/chunks/chunks.jsonl]
  end

  subgraph Vector[向量索引层]
    E --> F[index_qdrant.py\nembedding + upsert]
    F --> G[(Qdrant Collection)]
  end

  subgraph Query[查询/问答层]
    Q[用户问题] --> QE[qa.py / web_app.py\nembedding(question)]
    QE --> G
    G --> H[top-k 证据 chunks]
    H --> LLM[LLM\n基于证据回答]
    LLM --> OUT[答案 + 引用]
  end
```

## 架构图（演进：LlamaIndex → LangGraph）

```mermaid
flowchart TB
  A[input/ 原始文档] --> B[ingest_local.py]
  B --> C[data/local_export/input + meta]
  C --> D[chunk_local.py]
  D --> E[chunks.jsonl]
  E --> V[(向量库\nQdrant/Milvus/pgvector)]

  subgraph LIX[LlamaIndex（索引/查询编排层）]
    V --> R[Retriever]
    R --> S[Synthesizer\n(回答/总结)]
    S --> CIT[答案 + 引用]
  end

  subgraph LG[LangGraph（多步流程编排，可选）]
    U[用户问题] --> ROUTE[路由/分解/追问]
    ROUTE --> LIX
    LIX --> CHECK[自检/引用校验]
    CHECK --> FINAL[最终答案 + 引用]
  end
```

## 功能组件清单（便于扩展）

- **数据源连接器（Connectors）**
  - 本地文件（已实现）
  - 飞书云空间（待实现）
  - 其他：Confluence/Notion/Git/DB（可扩展）
- **解析与清洗（Parsers/Cleaners）**
  - Markdown/TXT/HTML（已实现基础版）
  - PDF/DOCX（已实现基础版）
  - 详细解析策略见 `docs/document_type_parsing.md`
  - 正文提取/去导航栏/去重复（待增强）
- **Chunker（切分器）**
  - 标题层级切分 + 段落兜底（已实现基础版）
  - 代码块/表格/FAQ 专项切分（待增强）
- **Embedding（向量化）**
  - 远程 OpenAI 兼容接口（已实现）
  - 本地模型（可选增强）
- **Vector Store（向量库）**
  - Qdrant（已实现）
  - Milvus/pgvector（可替换）
- **Query Pipeline（检索与回答）**
  - 单次问答（已实现 CLI/Web）
  - LlamaIndex Query Engine（计划）
  - LangGraph 多步流程（计划）
- **Governance（治理/观测）**
  - 增量台账（manifest 快照/历史治理，待增强）
  - 引用回链与可解释性（已实现基础版）
  - 评测集与指标（待补齐）

## 向量库结构（Qdrant Collection Contract）

Qdrant 的 payload 本质上是“半 schema-less”（可以直接写 JSON），但为了让索引/检索稳定、便于后续做过滤与解释，需要约定一个最小结构。

### Collection

- 名称：**每个 workspace 一个 collection**，来自 `workspaces.qdrant_collection`（建议 `ws_<uuid>`）
- alias：可中文展示，来自 `workspaces.qdrant_alias`
- 向量维度：由 `embedding.model` 决定（索引任务会先探测向量维度并创建/校验）
- 距离度量：`config.yaml` → `qdrant.distance`（默认 `Cosine`）

### Point ID

- `point.id = uuid5(chunk_uid)`（UUID 字符串），保证重复索引是幂等 upsert

### Payload 字段（最小约定）

- `chunk_uid`：`string`（用于回查 Postgres `chunks`）
- `document_id`：`string`
- `document_version_id`：`string`
- `offset_start` / `offset_end`：`int`（可选，用于排错/定位）

### Payload 索引（可选）

为后续“按 doc_id/source_uri 过滤、排错”创建 keyword 索引（MVP 已在创建 collection 时自动创建）：
- `doc_id`、`source_uri`、`chunk_id`

---

# 下一版架构决策（讨论结论 / 重构目标）

本节记录 2026-01 的讨论结论，用于指导从“单脚本 MVP”向“可多工作空间 + 多轮对话 + 可治理”的工程化重构。当前不着急动工写代码，先对齐目标与边界。

## 关键决策

- **后端框架**：采用 **FastAPI**（替代/弱化 Streamlit 作为主入口；Streamlit 可保留为 demo）。
- **Qdrant 组织方式**：**每个 workspace 对应一个 collection**（隔离简单、便于回收/迁移/权限治理）。
- **对话存储与记忆**：**Postgres 作为消息/会话的真相来源（SoT）**，**Redis 作为缓存 + LangGraph checkpoint**（降低延迟、支撑长对话状态机）。
- **Meta 数据落库**：把当前 `data/local_export/meta/*.json` 与增量台账能力迁移到 **Postgres**；本地文件系统仅保留“可选的原文缓存/临时产物”。
- **关系数据库**：采用 **Postgres** 承载业务数据与治理数据（workspace/doc/version/chunk/task/conversation/message 等）；数据结构详见 `docs/data_model.md`；任务系统详见 `docs/task_system.md`。
- **长对话与编排**：使用 **LangGraph** 实现多轮对话、上下文窗口管理、工具链路编排（检索/回答/自检/更新记忆等）。
- **LangGraph 运行时编排**：以对话框（conversation）为维度；不做 query rewrite；按 token 控制窗口；同 conversation 不允许并发提问；详见 `docs/langgraph_flow.md`。
- **UI 形态**：目标是 **NotebookLLM 风格的 workspace/namespace 工作空间**（卡片式入口、来源聚合、会话面板）；前端建议独立实现（非 Streamlit 复刻）。
- **文档预览**：在 workspace 详情页支持点击预览与下载原文件，便于核对索引内容。
- **前端技术方案（MVP）**：采用 **Next.js（React + TypeScript）+ TailwindCSS + shadcn/ui**，与 FastAPI 通过 REST 对接；详见 `docs/frontend_ui.md`。

## 多轮对话与记忆（Redis + Postgres）

### 分层记忆（推荐）

- **短期记忆（Short-term）**：最近 N 轮消息原文（Redis 缓存），用于当前上下文窗口拼接。
- **长期记忆（Long-term）**：会话摘要、用户偏好、任务状态（Postgres 保存；Redis 可缓存）。
- **检索式记忆（可选增强）**：将历史对话（或摘要片段）向量化，按 workspace + conversation 检索相关历史片段补充上下文，避免无限堆消息导致窗口溢出。

### 数据一致性原则

- Postgres 保存完整消息与关键派生字段（摘要/状态），保证可追溯与可重放。
- Redis 仅缓存：最近消息、摘要、LangGraph checkpoint、热点 workspace 配置等；缓存失效可从 Postgres 恢复。

## Workspace 与索引生命周期（增量更新）

### Workspace 行为目标

- 一个 workspace 可持续添加/更新/删除文档来源；
- 文档变化后只对“受影响的文档/切片”进行 chunk 与 embedding 更新；
- Qdrant collection 支持幂等 upsert，并能删除旧版本 chunks，避免“脏向量”干扰检索。

### 差分粒度（已确认：文件级）

- **文件级差分**：以“文件内容 hash/mtime/size 变化”为触发条件；一旦文件变更，视为该文档全量重建：
  - 重新 ingest（更新文档版本与元数据）
  - 重新 chunk（生成该文档全部 chunks）
  - 重新 embedding + upsert（写入向量库）
- 优点：实现最简单、工程风险最低；缺点：小改动也会触发该文档全量重算（可接受作为第一版）。

### 版本/删除策略（本版本选型：C；保留 A/B 作为后续演进）

- 文档层：为文档维护 `content_hash` 或 `version_id`；
- 切片层：chunk_id 引入版本（如 `chunk_<doc_id>_<version>_<idx>` 或用 `(doc_id, chunk_index, version_id)` 组合保证稳定性）；
- 向量层：写入 payload 最少包含 `workspace_id / doc_id / version_id / chunk_id / source_uri / offset_*`；
- 旧版本回收（**方案 C：强一致/直接删除**，本版本采用）：
  - 当新版本 chunks 写入成功后，按 `doc_id + previous_version_id` 批量删除旧 chunks（Qdrant filter delete）。
  - 风险：若“写入新版本”和“删除旧版本”之间发生失败，需要任务具备可重试与幂等（以避免短暂缺失或重复）。
- 双版本并存（方案 A/B，记录为后续演进）：
  - 先写新版本并完成校验，再切换“当前版本指针/active 标记”，旧版本异步清理；
  - 只要检索时过滤到“active/current_version”，双版本并存不会污染搜索结果，但会增加短期存储成本。

## Meta 数据迁移到 Postgres（可行性与范围）

### 迁移建议（范围划分）

- **必须入库**：workspace、document、document_version、ingestion_run、chunk（offset/title_path/source_uri/hash 等）、conversation、message、memory_summary。
- **是否保留本地文件**：原文可选（1）继续落盘并存路径（便于回链/调试）；或（2）存对象存储（后续）；不建议长期依赖 `data/` 目录作为主数据源。

## LangGraph 负责的对话/检索编排（目标形态）

一个典型 graph（示意）：
- `load_session` → `load_memory` → `rewrite_question` → `retrieve_chunks` → `answer_with_citations` → `verify_citations` → `update_memory` → `persist_messages`

说明：
- `retrieve_chunks` 读取“workspace 对应的 Qdrant collection”；
- `update_memory` 负责摘要压缩/状态更新，并写入 Postgres，同时更新 Redis 缓存；
- checkpoint 存 Redis（或 Postgres），用于断点恢复与调试。

## 工程化重构：模块化与 DDD 边界（指导原则）

## 查询链路（检索仍需要 chunks）

查询时向量召回来自 Qdrant，但回答与引用展示仍需要 Postgres 的 `chunks`：

- Qdrant 负责：按 query embedding 召回 `top-k`（返回 `score + payload(少量字段)`）。
- Postgres `chunks` 负责：提供 chunk 正文 `text` 与回链元数据（`source_uri/title_path/offset_*` 等）。
- 推荐做法：Qdrant payload 只放检索必要字段（如 `chunk_uid/doc_id/version_id/offset_*`），召回后用 `chunk_uid` 查询 `chunks` 补全内容，再拼 prompt/展示引用。

当前实现已迁移到 `document_graph/` 包，建议按分层模块沉淀：

- **Domain**：Workspace、Document、Chunk、Conversation、Message 等实体与值对象。
- **Application**：用例服务（IngestDocuments / ChunkDocuments / IndexEmbeddings / ChatWithCitations）。
- **Infrastructure**：Postgres repositories、Redis cache、Qdrant vector store、OpenAI-compatible client。
- **Interfaces**：FastAPI routes（后端 API）与前端 UI（NotebookLLM 风格）。

> 目标：chunk 阶段 / embedding 阶段 / 数据保存阶段 / 提问阶段都可独立演进、可测试、可观测。

## 开放问题（待下一轮讨论确认）

## 已确认的补充约束（来自后续讨论）

- **Workspace 入口页 UI 参考**：NotebookLLM 风格（见讨论截图）。
  - 页面包含“精选/最近打开”的 workspace 列表；
  - workspace 以卡片形式展示（标题、最近打开时间、来源数量等），支持新建与排序/筛选。
- **Qdrant collection 命名规范**：
  - collection 名称必须为英文（建议使用 `ws_<uuid>` 或 `ws_<slug>_<shortid>` 这类稳定规范）；
  - 允许为 collection 绑定 **alias（可中文）**，用于 UI 展示与人类可读性。
- **Workspace 删除策略（本版本）**：删除 workspace 需要 **直接删除对应 Qdrant collection（含 alias）**；后续版本可演进为软删除 + 审计保留。

## 待讨论（后续继续对齐）

- “进入 workspace 后”的页面信息架构：来源面板/会话面板/文档详情/引用定位的布局与交互（需要更完整的 workspace 内页参考）。
- Workspace 的“来源”类型边界：仅本地文件？何时引入飞书/Confluence/Notion 等连接器？
- **文档更新后的增量流程细节（部分已确认）**：
  - 已确认：文件级差分；删除策略采用方案 C（新版本写入成功后删除旧版本 chunks）。
  - 已确认（MVP）：采用异步队列/worker 执行增量流水线（**Celery + Redis**）。
    - **任务粒度（已确认）**：上传/更新文档后，仅创建 **一个“文档索引任务” task**，串行执行以下阶段（用 `stage/progress` 记录进度）：
      - `persist_meta`：写入/更新 `documents`，并创建 `document_versions`（**必须**写入，否则无法稳定回链、做版本治理与增量更新）。
      - `chunk`：生成 chunks，并写入 `chunks`（用于检索结果补全与引用展示）。
      - `embedding_upsert`：计算 embedding 并 upsert 到 Qdrant（workspace collection）。
      - `delete_old`：新版本写入成功后，按 `doc_id + previous_version_id` 删除旧版本向量（方案 C）。
    - task 内部用 `stage/progress` 字段记录当前阶段与进度；不在数据库中拆分为多个子 task（避免过度细粒度，MVP 优先）。
    - task 失败允许重试；MVP 先保证“可追踪 + 可人工介入”，幂等与并发细节在实现时按用例补齐。
  - 后续：接入监控与告警（metrics/tracing + failure alerting）；在 MVP 先保证任务可追踪、可重试、可人工介入。

---

# 重构 TODO 清单（MVP 优先级）

> 本节是后续逐步实现的 checklist（先对齐设计与数据结构，不急于编码）。

- 数据结构详见：`docs/data_model.md`
- **实施顺序（建议）**：
  1) **目录结构治理 + `document_graph/` 模块化**（优先保证核心逻辑集中在包内，避免再引入脚本入口）
  2) Postgres 表结构与迁移脚本（先把 `workspaces/sources/documents/document_versions/chunks/tasks/conversations/messages` 跑通）
  3) Qdrant workspace contract（collection/alias 的创建/删除与命名规范；workspace 删除时 drop collection）
  4) FastAPI 骨架与基础 API（workspace/conversation/task/upload/chat 的最小接口与错误码约定）
  5) 文件上传与存储抽象（本地目录或对象存储，写入 `storage_uri`；上传完成触发索引 task）
  6) Celery+Redis 任务系统（`document_index` task：`persist_meta -> chunk -> embedding_upsert -> delete_old`，状态回写 Postgres）
  7) LangGraph 对话编排接入（按 `docs/langgraph_flow.md`：session、token 窗口、检索+引用、消息/摘要落库）
  8) UI（NotebookLLM 风格）最小闭环（workspace 列表/详情、上传、对话、引用展示、task 状态）

- **MVP 范围（要做）**：
  - Workspace：创建/列表/进入/删除（删除时 drop 对应 Qdrant collection + alias）
  - Source：仅支持 `local_upload`（上传文件作为来源）
  - 文档索引：文件级差分；单 `document_index` task 串行 `persist_meta -> chunk -> embedding_upsert -> delete_old`
  - 检索问答：Qdrant 召回 + Postgres `chunks` 补全文本与引用；输出 machine-readable refs
  - 多轮对话：conversation 维度 session；token 窗口裁剪 + 摘要；同 conversation 禁并发（Redis lock）
  - UI：NotebookLLM 风格的最小两页（workspace 列表页 / workspace 详情页）
    - 列表页：默认展示全部 workspace 的前 5 个；支持按名称搜索；“最近打开”来自本地缓存（localStorage）。
    - 详情页：左侧展示文档（文件）列表，默认分页前 10 个；中间对话面板；不做资料勾选（workspace→collection 直接检索）。

- **非 MVP（暂不做/后续）**：
  - 连接器扩展：飞书/Confluence/Notion/Git/DB 等
  - 更复杂的检索与推理：query rewrite、rerank、混合检索、GraphRAG/Neo4j
  - 软删除与审计：workspace/doc 的软删、历史回滚、全链路审计
  - 完整可观测性：metrics/tracing/告警体系（MVP 先保证 task/对话可追踪、错误可回写）
  - 并发提问：同 conversation 并发处理与冲突合并
- **定义 Meta 数据结构（Postgres）**：workspace/document/document_version/chunk/source（来源）等表与字段，明确主键、唯一约束、索引与必要枚举值。
- **定义 Task 数据结构（Postgres）**：task/run/step 的最小字段集（状态、进度、错误、重试次数、幂等键、触发者、时间戳），与 Celery task_id 的关联方式。
- **Task 粒度约束（MVP）**：每次“文档上传/更新”只创建一个 `task`，其 `stage` 在 `persist_meta -> chunk -> embedding_upsert -> delete_old` 之间流转；需要更细审计时再追加 task_event/task_log（后续）。
- **Workspace 与 Qdrant contract**：
  - 每 workspace 一个 collection；
  - collection 英文命名规范（如 `ws_<uuid>`）；
  - alias 可中文用于展示；
  - 删除 workspace 直接 drop collection（本版本），软删除作为后续演进。
- **文件上传与存储抽象**：上传 API、workspace 分区目录/对象存储（S3/MinIO）选型落表（storage_uri），以及回链定位策略。
- **增量流水线步骤拆分**：`ingest -> chunk -> embed/upsert -> delete_old` 的 step 定义、输入输出契约、失败重试与幂等策略（文件级差分）。
- **Qdrant 基础能力封装**：collection/alias 生命周期、按 `doc_id+version_id` 删除旧 chunks 的 filter delete、检索统一入口（按 workspace collection）。
- **对话与记忆数据结构**：conversation/message/memory_summary（Postgres）+ Redis 缓存键约定（最近 N 轮、摘要、LangGraph checkpoint）。
- **FastAPI 接口草案**：workspace/doc/task/chat 的 API 边界、鉴权（哪怕 MVP 先留占位）、分页与筛选。
- **UI 信息架构（NotebookLLM 风格）**：
  - workspace 列表页（精选/最近打开/新建）；
  - workspace 详情页（来源/上传、对话面板、引用定位）。

## 分支：媒体能力 + 体验闭环（拟 v0.2）

本轮已确认的改进方向与 TODO 清单统一维护在：

- `docs/branch_media_and_ux_improvements.md`

该分支包含：
- Redis 缓存（最近消息/摘要）与历史对话回放（含引用持久化）
- 前端 download + PDF/DOCX 富预览
- 文件夹批量上传与树形展示
- 图片双通道索引（OCR + 多模态向量）

## `scripts/`（已移除）

旧版脚本入口已移除；当前以 `document_graph/` 包作为唯一实现入口，后续模块化演进基于该包继续推进。
