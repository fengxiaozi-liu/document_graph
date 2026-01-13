# document_graph

本仓库提供一个“本地文档 → 向量检索 → 生成式问答（RAG）”的最小可用闭环，支持把 LLM 与 embedding 都配置为远程 OpenAI 兼容接口（例如 Qwen 兼容模式）。

## 新版 MVP（重构中：Workspace + FastAPI + Postgres + Redis + Celery + Qdrant + LangGraph）

本版本目标（见 `docs/architecture.md`）：
- Workspace（每个 workspace 一个 Qdrant collection，alias 可中文展示）
- 文档上传（`local_upload` source）→ 异步索引任务（`document_index`：`persist_meta -> chunk -> embedding_upsert -> delete_old`）
- 对话问答（conversation 维度 session，token 窗口控制，同 conversation 禁并发）

数据与实现细节：
- 数据模型：`docs/data_model.md`
- 任务系统：`docs/task_system.md`
- LangGraph 编排：`docs/langgraph_flow.md`

### 运行（docker compose）

1) 准备配置（LLM/Embedding/Qdrant URL 可用 env 覆盖）：

```
cp config.example.yaml config.yaml
```

2) 启动依赖与服务：

```
docker compose -f docker-compose.yaml up -d --build
```

（调试模式：只启动依赖，API/Worker 用 VSCode 启动）

```
docker compose -f docker-compose.deps.yaml up -d
```

3) 打开 API 文档：

- Swagger：`http://localhost:8000/docs`

### 最小调用流程（API）

1) 创建 workspace：`POST /workspaces`
2) 上传文档并触发索引：`POST /workspaces/{workspace_id}/documents/upload`
3) 发起对话问答：`POST /workspaces/{workspace_id}/chat`

## 配置与环境变量

配置文件：`config.yaml`（复制 `config.example.yaml`）。

支持用环境变量覆盖（用于容器/部署）：
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`
- `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL`
- `QDRANT_URL`
- `POSTGRES_URL`
- `REDIS_URL`（当前 Redis 开启密码：`document_graph`）
