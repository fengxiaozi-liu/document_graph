## Alembic 迁移说明

本目录保存 Postgres 的结构迁移脚本，表结构与字段语义以 `docs/data_model.md` 为准。

### 当前约定

- `document_versions.mime_type` / `document_versions.file_ext` 用于解析器选择与类型识别。
- 未支持的文件类型应在解析阶段被拒绝或降级，不应进入索引流程。

### 迁移记录

- `0002_backfill_docver_types`：为历史 `document_versions` 回填 `file_ext` 与 `mime_type`。
