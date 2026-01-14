# document_graph

本仓库提供一个“本地文档 → 向量检索 → 生成式问答（RAG）”的最小可用闭环，支持把 LLM 与 embedding 都配置为远程 OpenAI 兼容接口（例如 Qwen 兼容模式）。


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
