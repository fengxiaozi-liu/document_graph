# 本地文档 MVP 跑通（最小闭环）

## 1) 准备输入目录

在仓库根目录创建 `input/`，放入你的本地文档（MVP 仅支持）：
- `.md`
- `.txt`
- `.html` / `.htm`

示例：

```
input/
  手册.md
  FAQ.txt
  wiki/
    架构.html
```

## 2) 运行本地导入

在仓库根目录执行：

```
python3 scripts/ingest_local.py --input-dir input --output-dir data/local_export
```

产物目录：
- `data/local_export/input/`：用于给 GraphRAG 的输入目录（文件名为稳定 `doc_id`）
- `data/local_export/meta/`：每个文档一份元数据（回链用）
- `data/local_export/manifest.jsonl`：同步清单（增量用）

## 3) 增量更新

再次运行同一命令即可；脚本会基于 `mtime/size`（以及内容 hash）跳过未变更文件。

## 4) 下一步：接入 GraphRAG

把 GraphRAG 的输入目录指向：
- `data/local_export/input`

如果你已经有 GraphRAG 项目结构，也可以把 `data/local_export/input` 软链到 GraphRAG 的 `input/` 目录。

