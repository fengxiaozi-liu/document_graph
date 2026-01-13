# 本地文档 → GraphRAG（MVP）设计文档

## 目标与范围

**目标**：从本地目录批量读取文档，整理为适合索引的离线文件（优先 `Markdown/HTML/TXT`），并生成元数据文件，作为 GraphRAG 的 `input/` 数据源（后续再进入向量索引/GraphRAG 索引/可选 Neo4j）。

**MVP 假设**：
- 文档全员可见，不做细粒度 ACL（统一 `acl=public`）。
- 数据源是本地文件系统目录（先跑通端到端）。
- 先覆盖纯文本/半结构化文档（`md/txt/html`）。`pdf/docx` 作为后续增强。

**非目标（MVP 不做）**：
- 基于用户身份的真实权限隔离。
- 实时同步（先做增量更新的批处理）。
- OCR、复杂版面恢复、图片/附件解析。

## 产出物（落地接口）

同步完成后，本地目录建议如下：

```
data/
  local_export/
    input/
      <doc_id>.md              # 或 .html/.txt
    meta/
      <doc_id>.json            # 每个文档一份元数据
    manifest.jsonl             # 汇总清单（便于增量/审计）
```

每个文档至少保证：
- 可索引内容：`data/local_export/input/<doc_id>.(md|html|txt)`
- 可回链元数据：`source_uri`（本地相对路径）、`updated_at`、`title` 等

> 说明：本文件名保留为 `feishu-drive-ingestion-design.md` 是历史原因；MVP 已调整为本地文档模式，飞书同步作为后续扩展（见“后续：飞书云空间文件夹同步”）。

## Chunk 策略（按标题 + 按内容混合切段）

**为什么要做 chunk**：总结问答不是“把整篇文档塞给大模型”，而是先检索到少量高相关“证据片段”再总结；chunk 质量会直接决定“召回准不准、引用能不能定位、答案稳不稳”。

### Chunk 的目标

- 每个 chunk 尽量语义完整（不要把步骤/定义拆断）
- 每个 chunk 不要过长（否则 embedding 召回和上下文拼接成本高、效果不稳）
- chunk 必须可回链到原文位置（文档/标题路径/偏移/页码等）

### 推荐参数（中文为主的默认值）

- `target_tokens`: 500（目标长度）
- `max_tokens`: 1000（超过必须再切）
- `overlap_tokens`: 80（滑窗重叠，避免跨段断裂）

> 注：这里的 tokens 是“近似 token”，工程上可以用字符数近似（例如 1 token ≈ 1–2 个中文字符/英文 3–4 个字符），后续再用模型 tokenizer 精确化。

### 切分规则（从高到低优先级）

1. **标题层级**（适用于 Markdown/HTML）
   - Markdown：按 `# / ## / ###` 建 `title_path`
   - HTML：按 `h1/h2/h3` 建 `title_path`（先做正文提取，避免导航栏污染）
2. **内容边界**（兜底）
   - 段落边界（空行）、列表（项目符号/编号）、代码块、引用块、表格块尽量保持为整体
3. **长度兜底**
   - 如果一个 section 仍然超过 `max_tokens`，使用滑窗按长度切分，并加 `overlap_tokens`

### Chunk 产物建议（便于向量检索与引用）

建议在 `data/local_export/` 下新增：

```
data/local_export/
  chunks/
    chunks.jsonl
```

每行一个 chunk（JSON），字段建议：
- `chunk_id`：例如 `chunk_<doc_id>_<index>`
- `doc_id`
- `title_path`：`["一级标题","二级标题"]`（无则空数组）
- `offset_start` / `offset_end`：在规范化文本中的字符偏移
- `chunk_type`：`text/list/code/table` 等
- `text`：用于 embedding 的纯文本
- `source_uri`：用于回链（来自 doc meta）

## Embedding 与向量库（检索底座，可 Docker 部署）

### Embedding 的作用

Embedding 把每个 chunk 的 `text` 编码成向量；查询时把问题也编码成向量，通过向量相似度召回最相关的 chunks。

对总结问答而言，embedding/向量检索解决的是“在几千份文档里，先找出最相关的少量证据”，后续大模型再在这些证据上做总结与回答，并引用来源。

### 为什么需要向量库

- 支持高效相似度检索（top-k）
- 支持元数据过滤（例如项目/时间/来源）
- 支持增量写入与持久化

### Docker 部署建议（MVP 选型）

MVP 推荐用 Qdrant（轻量、API 简单、Docker 一条命令可跑通）：

```
docker run -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/data/qdrant:/qdrant/storage \
  qdrant/qdrant:latest
```

或直接使用仓库内的 `docker-compose.yml`：

```
docker compose up -d qdrant
```

后续你的索引流程会多两步：
1. 对 `chunks.jsonl` 逐条计算 embedding
2. 写入 Qdrant collection（向量 + payload：`doc_id/chunk_id/title_path/source_uri` 等）

> 注：embedding 模型可以用本地模型（如 `sentence-transformers` 系列）或云 API（更省维护）。MVP 先以“能跑通”为准，先选一个稳定可用的 embedding 方案即可。

## 核心流程

### 1) 遍历输入目录

从起始 `INPUT_DIR` 开始，递归列出文件：
- 记录每个条目的相对路径、扩展名、mtime、文件大小
- 过滤：只处理允许的扩展名（MVP：`md/txt/html/htm`）

### 2) 增量判断（避免全量处理）

建议的增量策略（从轻到重）：
1. 以本地文件 `mtime + size` 为主：manifest 中若同一 `doc_id` 的 `mtime/size` 未变化，则跳过
2. 计算 `content_hash`：用于检测极端情况下时间戳不变但内容变化

manifest 记录字段建议：
- `doc_id`
- `relative_path`
- `file_type`（md/txt/html）
- `mtime`
- `size_bytes`
- `export_format`
- `local_path`
- `content_hash`
- `synced_at`

### 3) 规范化与落盘

对支持的扩展名文件：
1. 读取原文件内容（保持原始格式：`md/html/txt`）
2. 为每个文件分配稳定 `doc_id`（建议基于相对路径做 hash）
3. 将内容复制到 `data/local_export/input/<doc_id>.<ext>`
4. 写入 `meta/<doc_id>.json` 与 `manifest.jsonl`

### 4) 元数据写入（GraphRAG/检索回链）

元数据字段（MVP 必选）：
- `doc_id`：建议 `local_<hash(relative_path)>`
- `title`：文件名（不含路径）
- `source_type`：`local_fs`
- `source_uri`：相对路径（用于回链）
- `updated_time`：本地文件 mtime（ISO8601 或 epoch 原样保存均可）
- `export_format`：`md|html|txt`
- `local_path`：本地 input 文件路径
- `acl`：固定 `public`（MVP）

可选字段（建议保留以便后续扩展）：
- `owner_id`、`creator_id`
- `path`：在云空间中的逻辑路径（便于前端展示）
- `labels/tags`：如项目名

## 文档类型与导出格式建议

### Markdown/TXT/HTML（MVP）
- Markdown：天然标题层级/列表/代码块，最推荐
- TXT：适合日志/纯文本规范
- HTML：保留结构较好，但建议后续增加 HTML 清洗与正文提取

### Sheet / Bitable（后续扩展）
- Sheet：优先导出 CSV（每个表/每个 sheet），再转“表格描述 + 表头/行块 chunk”
- Bitable：优先 API 拉取 records（JSON），再转“字段说明 + 关键记录摘要”

## 错误处理与鲁棒性

- **限流/重试**：对 429/5xx 指数退避；所有 API 调用都需要可重试
- **导出失败**：记录失败原因到 manifest，并允许重跑
- **下载中断**：支持断点续跑（以文件存在 + hash 校验）
- **幂等**：同一版本重复运行不产生重复文件；manifest 可追溯

## 安全与配置

配置来源：环境变量或命令行参数（MVP 建议命令行参数）
- `INPUT_DIR`（默认 `./input`）
- `EXPORT_FORMAT`（默认“保持原扩展名”；可选强制 `txt` 作为统一格式）
- `OUTPUT_DIR`（默认 `data/local_export`）

敏感信息：
- 仅本地模式不涉及密钥

## 与 GraphRAG 的对接方式（MVP）

GraphRAG 通常从一个 `input/` 目录读取原始文件。对接策略：
- 将本设计产物中的 `data/local_export/input/` 作为 GraphRAG 输入目录
- 元数据：GraphRAG 若不直接消费，可在后处理阶段把 `meta/*.json` 合并到 GraphRAG 的文档元数据字段中（用于引用回链）

## 本版本能否实现 GraphRAG？需要部署什么组件

**可以实现**，但需要明确：GraphRAG 不是“只部署一个数据库”，而是一套“离线索引 + 在线查询”的流程。按本版本（本地文档 MVP）建议的最小组件如下：

### 必要组件（MVP）

- **Python 运行环境**：用于跑本地导入、chunk、embedding、GraphRAG 索引与查询脚本
- **LLM（用于摘要/回答）**：可用任意兼容的 API（或本地模型）；用于生成社区摘要、最终回答
- **Embedding 模型**：用于向量化 chunks（本地或云都可）
- **向量库**：推荐 Qdrant（Docker），存储 chunk 向量并提供相似度检索

### 可选组件（增强但非必需）

- **Neo4j**：用于解释图/知识探索/排错（对“总结问答效果”不是第一优先级）
- **全文检索（BM25）**：做混合检索（hybrid），提升某些精确查找的召回

### 典型链路（建议你按这个顺序逐步落地）

1. `ingest_local.py`：产出 `data/local_export/input + meta + manifest`
2. chunk：产出 `data/local_export/chunks/chunks.jsonl`
3. embedding：写入向量库（Qdrant）
4. GraphRAG 索引：基于文档/实体关系构建社区，并生成社区摘要（可先小规模跑通）
5. 查询：`query -> 向量召回 chunks ->（可选）社区摘要 -> LLM 生成答案 + 引用`

## 实施里程碑

1. 实现输入目录遍历 + 复制/规范化到本地（仅全量）
2. 增量同步（基于 mtime/size + manifest）
3. 增加 chunk 产物（`chunks.jsonl`）
4. 增加 embedding + 向量库（Qdrant Docker）
5. 接入 GraphRAG（社区摘要 + 查询）
6. 扩展类型：pdf/docx（解析或导出）
7. 后续接入飞书云空间同步

## 后续：飞书云空间文件夹同步（扩展）

当本地模式跑通后，飞书同步可以作为“数据采集层”接入，产出与本地模式一致的 `data/*/input + meta + manifest`，从而不影响下游 GraphRAG/向量索引/问答层。
