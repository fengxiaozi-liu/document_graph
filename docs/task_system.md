# Task 系统（Celery + Redis + Postgres）

本文件定义 document_graph MVP 的异步任务系统，用于文档上传/更新后的索引流水线执行、状态回写、重试与排错。

## 目标

- 上传/更新文档后触发异步索引，不阻塞 Web 请求。
- 任务状态可追踪（UI 可展示进度/错误），失败可重试、可人工介入。
- 任务幂等：重复触发不会造成重复数据或脏向量（MVP 先覆盖核心路径）。

## 技术选型（已确认）

- 队列/执行：Celery
- Broker/Backend：Redis
- 状态真相来源（SoT）：Postgres（`tasks` 表）

## 任务粒度（MVP）

每次“文档上传/更新”仅创建 **一个 `document_index` 任务**，任务内部按 `stage` 串行执行：

1) `persist_meta`：写入/更新 `documents`，创建 `document_versions`
2) `chunk`：生成 chunks 写入 `chunks`
3) `embedding_upsert`：计算 embedding 并 upsert Qdrant（workspace collection）
4) `delete_old`：新版本写入成功后删除旧版本向量（方案 C）

> 任务不拆成多个子 task；细粒度审计通过 task 的 `stage/progress/error`（以及后续可选的 task_event/task_log）实现。

## Postgres：`tasks` 表字段（摘要）

以 `docs/data_model.md` 为准，关键字段含义如下：

- `type`：MVP 固定为 `document_index`
- `status`：`pending|running|succeeded|failed|canceled`
- `stage`：`persist_meta|chunk|embedding_upsert|delete_old`
- `progress`：0..1（可选，MVP 可先按阶段粗粒度更新）
- `idempotency_key`：用于去重与幂等（建议：`workspace_id:document_id:content_hash:type`）
- `celery_task_id`：Celery 侧 task id（用于排错/定位 worker 日志）
- `input/result/error`：结构化入参/结果/错误（jsonb）
- `attempt/max_attempts`：重试计数
- `created_at/started_at/finished_at/updated_at`：时间戳

## 触发方式（MVP）

- 上传接口成功落存储后，创建/更新 doc 记录并创建一条 `tasks` 记录（`status=pending`），随后投递 Celery 任务。
- 重复上传同一内容（同 `content_hash`）时，使用 `idempotency_key` 去重：如果已有 `succeeded/running` 的相同 key，则直接复用现有 task。

## 失败与重试（MVP）

- 失败时：
  - 更新 `tasks.status=failed`，写入 `error`（code/message/stack/extra）
  - 保留中间产物（Postgres 版本记录、chunks、Qdrant 新写入点）以便排错
- 重试：
  - 允许对 `failed` 任务进行重试（新建 task 或复用同 key 取决于实现；MVP 推荐新建 task 并保留父子关系，后续再增强）

## 幂等与一致性（MVP 要求）

- `persist_meta`：通过 `(workspace_id, source_id, external_key)` 唯一约束 upsert 文档；版本通过 `(document_id, content_hash)` 去重。
- `chunk`：以 `(document_version_id, chunk_index)` 或 `chunk_uid` 唯一约束防止重复写。
- `embedding_upsert`：Qdrant point id 用 `chunk_uid` 派生稳定 UUID（uuid5），保证 upsert 幂等。
- `delete_old`：按 `document_id + previous_version`/`document_version_id` filter delete，重复执行应安全。

## 监控与告警（后续）

- 指标：task 成功率、耗时分布、队列长度、重试次数、失败原因分布
- 链路：OpenTelemetry tracing（FastAPI + Celery）
- 告警：连续失败、队列堆积、索引耗时异常

