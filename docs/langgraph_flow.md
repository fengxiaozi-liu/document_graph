# LangGraph 编排（对话/RAG 核心）

本文件定义 document_graph 下一版的 LangGraph 运行时编排：以“对话框（conversation）”为维度管理多轮对话状态，完成检索与回答，并把状态/记忆写入 Postgres + Redis。

## 设计边界（已确认）

- **不做 query rewrite**：检索 query 直接使用用户原始问题文本。
- **上下文窗口按 token 控制**：不按“最近 N 轮”固定截断；需要根据 token 预算动态裁剪与摘要。
- **会话维度**：LangGraph 以 `conversation_id`（对话框）为 key 管理状态；同一 workspace 可有多个 conversation。
- **不允许并发提问**：同一 `conversation_id` 同一时刻只允许一个 graph 运行（需要加锁）。

## 依赖与数据源

- LLM / Embedding：OpenAI-compatible HTTP
- Vector store：Qdrant（每 workspace 一个 collection）
- SoT：Postgres（messages / memory_summaries / chunks / workspaces / documents...）
- Cache + checkpoint：Redis（消息缓存、摘要缓存、LangGraph checkpoint、会话锁）

## Graph 输入 / 输出

输入（每次用户提问触发一次运行）：
- `workspace_id`
- `conversation_id`（可选；为空则创建新对话框）
- `user_message`（本轮用户输入）

输出：
- `answer`（给用户展示）
- `refs`（machine-readable 引用结构，引用到 chunk）
- 可选：`debug`（命中的 chunk_uids、token 统计等，供开发/排错）

## State（最小）

- `workspace_id`
- `conversation_id`
- `user_message`
- `history_messages`：用于拼接上下文的消息列表（从 Redis/DB 获取后裁剪）
- `memory_summary`：长对话摘要（从 Redis/DB 获取，必要时更新）
- `query_text`：用于检索的文本（= user_message）
- `retrieved_chunk_uids`：Qdrant 返回的标识列表
- `retrieved_chunks`：从 Postgres `chunks` 补全的 chunk（text+meta）
- `answer`
- `refs`

## 节点与流程（MVP）

0) `ensure_conversation`
   - 目的：基于用户进入 workspace 的行为创建/恢复“会话（session）”。
   - 约定：本项目的 session 以 **对话框 `conversation_id`** 为核心标识（同时也是 LangGraph 的 thread key）。
   - 创建规则（MVP）：
     - 若请求中带 `conversation_id`：视为继续该对话框（需校验其属于 `workspace_id`）。
     - 若未带 `conversation_id`：创建一条 `conversations` 记录并返回新的 `conversation_id`，标题可先置空，后续由首问或模型生成。
   - 前端建议：
     - workspace 列表页 → 进入 workspace 详情页时，展示最近对话框；用户点“新建对话”则不带 `conversation_id` 触发创建。
     - 将 `conversation_id` 保存在路由参数（例如 `/w/<workspace_id>/c/<conversation_id>`）或 localStorage，便于恢复会话。
   - 与鉴权的关系（占位）：
     - 若未来引入登录，则在 `conversations` 加 `user_id/owner_id` 或 ACL；MVP 可先不做。

1) `acquire_conversation_lock`
   - 目的：保证 **同一 conversation 不并发**。
   - 实现建议：Redis `SET lock:<conversation_id> <uuid> NX PX <ttl>`；运行结束释放；TTL 防止死锁。

2) `load_session`
   - 读取 `workspaces`，确认 workspace 存在，并得到 `qdrant_collection`（collection 英文名）。

3) `persist_user_message`
   - 向 Postgres `messages` 写入本轮用户消息（SoT）。
   - 同时刷新 Redis 缓存（conversation 最近消息列表）。

4) `load_memory`
   - 读 Redis：`history_messages`（最近消息）与 `memory_summary`（摘要）。
   - cache miss 回源 Postgres：`messages` / `memory_summaries`。

5) `build_context_by_tokens`
   - 核心：基于 token 预算生成 LLM 上下文：
     - 固定部分：system prompt +（可选）memory_summary + 本轮 user_message + evidence 模板开销
     - 可变部分：history_messages（从新到旧或从旧到新，按策略裁剪）
   - 当 token 超限时：
     - 先裁剪最旧的 history_messages
     - 若仍超限或历史过长：触发 `summarize_memory`（见第 9 步）

6) `retrieve_vectors`
   - `embed(query_text)` → Qdrant search（workspace collection）→ `retrieved_chunk_uids`
   - payload 推荐包含 `chunk_uid`，确保能回查 Postgres。

7) `hydrate_chunks`
   - 按 `retrieved_chunk_uids` 批量查询 Postgres `chunks`，得到 `text + source_uri/title_path/offset_*` 等引用信息。

8) `answer_with_citations`
   - 组装 prompt：system + memory_summary +（裁剪后的）history + evidence（chunks 文本，带编号）
   - 调用 LLM 生成答案。
   - 要求：答案只基于证据；证据不足必须说明不确定，并给下一步建议。

9) `update_memory_summary`（按需）
   - 触发条件建议：
     - 本轮 token 压力较大（多次裁剪仍接近上限）
     - 或对话累计消息超过阈值
   - 做法：把“较早的消息”摘要压缩到 `memory_summaries.summary`，并更新 Redis 缓存。

10) `persist_assistant_message`
   - 写入 Postgres `messages`（assistant）。
   - 引用 refs 建议存：
     - MVP：写入 `messages.metadata`（jsonb，若你们愿意加该列）
     - 或新增 `message_refs` 表（后续演进）

11) `release_conversation_lock`

## Session（对话框）生命周期（MVP）

- 创建：
  - 方式 A（推荐）：用户点击“新建对话”，后端创建 `conversations` 并返回 `conversation_id`。
  - 方式 B：用户首次提问且未带 `conversation_id` 时自动创建（与方式 A 等价，只是由首问触发）。
- 恢复：
  - 通过 workspace 详情页“最近打开对话框”列表进入，携带 `conversation_id`。
- 关闭：
  - 无需显式关闭；长期不活跃的对话可在 UI 侧归档/隐藏（后续）。

## API 约定（用于落地 session）

仅用于对齐（不代表最终路径）：

- `POST /workspaces/{workspace_id}/conversations` → `{ conversation_id }`
- `GET /workspaces/{workspace_id}/conversations?order=recent` → 最近对话列表（用于 workspace 内页）
- `POST /conversations/{conversation_id}/messages`（或 `POST /chat`）→ 触发 LangGraph 本轮运行并返回 `{ answer, refs }`

## Token 窗口策略（MVP 约定）

- 预算拆分建议（示意）：
  - `max_context_tokens`：模型上下文上限（按模型配置）
  - `reserved_for_output`：预留输出 tokens（例如 1024）
  - `reserved_for_evidence`：预留证据 tokens（例如 top-k * 平均 chunk tokens 的上限）
  - `available_for_history`：剩余 tokens 给 history + summary
- token 估算：
  - MVP 可先用近似估算（字符数/4）或现有 tokenizer
  - 后续再精确到模型 tokenizer（建议在基础设施层封装 `TokenCounter`）

## 并发与一致性

- **同 conversation 严格串行**：通过 Redis lock 保证；超时/异常要有 TTL 兜底。
- **跨 conversation 并行允许**：同一 workspace 的多个 conversation 可并发（取决于服务资源）。

## 失败处理（MVP）

- 任一步失败：返回可读错误给用户；并把错误写入日志/trace（后续接入告警）。
- 锁释放：使用 `finally` 确保释放；若进程崩溃，靠 TTL 自动释放。
