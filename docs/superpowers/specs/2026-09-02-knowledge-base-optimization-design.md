# 知识库优化改造 · 设计文档

日期：2026-09-02
状态：已评审待实现
前置文档：`docs/superpowers/specs/2026-08-11-knowledge-base-design.md`（知识库 v1 原始设计，本文是其重建）

## 1. 背景与目标

当前知识库（项目管理「知识库」tab）是 v1 的最小实现：文档**寄生在 `project_contexts` 表**（`source_type='knowledge'`），只有单篇富文本 CRUD。用户反馈的核心痛点：

1. **阅读不方便**——只有 720px 侧滑抽屉，无全屏阅读、无目录大纲、无锚点跳转、无排版调节。
2. **分类不清晰**——借用 AI-RAG 的 8 种 `context_type` 枚举当分类，且只能挂到「模块」下，无独立目录树、无标签。
3. **没有导入导出**——无法备份/迁移，无法批量导入外部文档。
4. **只有内联富文本**——没有「文件」概念，PDF/Word/Excel/图片无法上传与预览。
5. **体验不足**——无全文搜索、删除用原生 `confirm()`、无版本历史、无作者追溯。

本次改造目标：把知识库从「AI 记忆的一个副产物」升级为**独立、好用的团队知识中心**，同时**不破坏现有 AI 用例生成对知识文档的 RAG 召回**。

## 2. 范围

本轮四块全做：**阅读体验、分类与检索、导入导出、全格式文件托管与预览**。

分类体系走**独立目录树 + 标签**。数据模型**迁到独立表**。

明确不做（YAGNI，留待后续）：
- 向量 embedding 检索（现有关键词/全文检索够用，`embedding` 字段继续闲置）。
- 跨项目共享知识库 / 全局知识库。
- 在线协同编辑（多人同时编辑同一文档）。
- 评论、点赞、订阅等社交化功能。
- 服务端 Office→PDF 转换（改用前端 JS 库渲染，不装 LibreOffice）。

## 3. 架构决策

### 3.1 独立数据模型 + RAG 单向投影

知识库自建表，与 `project_contexts` 解耦。为保住「零成本喂 RAG」，纳入检索的文档在存盘时**单向投影**一行到 `project_contexts`：

```
knowledge_documents (人写/上传的知识)
        │  save / toggle include_in_rag
        ▼
project_contexts (source_type='knowledge', knowledge_document_id=FK)   ← RAG 投影
        │
        ▼
context_service.retrieve_context()  ← AI 用例生成召回，代码零改动
```

- 富文本文档：投影 `content`（去标签纯文本）。
- 文件文档：投影抽取出的文本（PDF 文本层 / docx 文本；抽不出则只投影标题，或不投影）。
- `include_in_rag=false` 或删除文档：删除对应投影行。
- 投影是**一对一、幂等 upsert**，键为 `knowledge_document_id`。

好处：AI 侧 `context_service` / 用例生成完全不用改；知识库表可以自由演进。

### 3.2 文件存储

复用本地磁盘，与 `data/reports`、`data/results` 一致的套路：

- 落盘路径：`data/knowledge/<project_id>/<attachment_uuid>.<ext>`。
- 下载**走鉴权路由**（校验项目访问权后返回文件流，见 §7），不做公开静态挂载——知识库文件可能含敏感需求资料。
- 路径锚点用 `_PROJECT_ROOT = Path(__file__).resolve().parent.parent`，禁止 `Path.cwd()`（trap #4）。
- 不引入 S3/对象存储（YAGNI；将来要扩再抽存储接口）。

### 3.3 预览策略（全部客户端渲染，服务端零转换）

| 类型 | 预览方式 |
|---|---|
| 图片 (png/jpg/gif/webp/svg) | `<img>` 原生 |
| PDF | 浏览器原生 `<embed>`/`<iframe>`，或 pdf.js（CDN 受限时打包进来） |
| Word (.docx) | 前端 `docx-preview` 库渲染 |
| Excel (.xlsx/.csv) | 前端 `SheetJS(xlsx)` 解析成表格 |
| Markdown (.md) | 复用现有 Markdown 渲染 |
| 其他/不支持 | 显示元信息 + 下载按钮兜底 |

## 4. 数据模型

新增表（SQLAlchemy 2.0 风格，JSON 列用 `database.base.JSONType`）：

### `knowledge_folders` — 独立目录树
```
id, project_id(FK, index), parent_id(FK self, nullable),
name(String255), sort_order(Integer), created_at, updated_at
```
多级目录，`parent_id=NULL` 为根级。替代现在借用的「模块树」。

### `knowledge_documents` — 文档主体
```
id, project_id(FK, index), folder_id(FK, nullable, index),
doc_type(String20: 'rich_text' | 'file'),
title(String255), content(Text, 去标签纯文本), content_html(Text, nullable),
context_type(String50)          # 保留，供 RAG 投影归类，默认 term_definition
include_in_rag(Boolean, default True),
is_pinned(Boolean, default False), sort_order(Integer),
author_id(FK users, nullable), editor_id(FK users, nullable),
created_at, updated_at
```

### `knowledge_tags` + `knowledge_document_tags` — 标签（多对多）
```
knowledge_tags:          id, project_id(FK), name(String64), color(String16, nullable), UNIQUE(project_id, name)
knowledge_document_tags: document_id(FK), tag_id(FK), PK(document_id, tag_id)
```

### `knowledge_attachments` — 文件附件
```
id, document_id(FK, index), filename(String255), mime(String128),
size_bytes(Integer), storage_path(String512), uploaded_by(FK users, nullable), created_at
```
`doc_type='file'` 的文档主文件也是一条 attachment；`doc_type='rich_text'` 的文档可挂多个附件。

### `knowledge_document_versions` — 版本历史
```
id, document_id(FK, index), title(String255), content_html(Text),
editor_id(FK users, nullable), created_at
```
每次编辑保存前，把旧内容快照进版本表（保留最近 N 条，N 默认 20，可配）。

### `project_contexts`（既有表）
新增列：`knowledge_document_id(Integer, FK knowledge_documents.id, nullable, index)`，作为 RAG 投影与源文档的关联键。

## 5. 迁移

数据迁移脚本 `database/migrations/data_migrations/migrate_knowledge_to_dedicated_tables.py`：

1. Alembic schema 迁移：建 5 张新表 + `project_contexts.knowledge_document_id` 列。
2. 遍历现有 `project_contexts WHERE source_type='knowledge'`：
   - 建对应 `knowledge_documents` 行（`doc_type='rich_text'`，搬 title/content/content_html/context_type/importance→include_in_rag）。
   - 原 `module_id` → 如需保留分组，在目录树里为每个模块建同名根目录并挂上（或落根级，实现时二选一，默认落根级 + 保留 module 引用为空，避免目录树被模块结构绑架）。
   - 回填 `project_contexts.knowledge_document_id` 指向新行（保留原投影行，不重复召回）。
3. 幂等：脚本可重复执行，已迁移的跳过。

**review 自动生成的 Alembic 迁移**（autogenerate 常漏 server_default / index，trap in CLAUDE.md）。

## 6. 分阶段交付

每阶段是一次可独立上线的增量，落地时各出一份实现 plan。

### 阶段 0 · 地基（后端）
- 5 张新表 + `project_contexts` 加列 + Alembic + 数据迁移脚本。
- `knowledge_service` 重写：CRUD 面向新表；`_sync_rag_projection(doc)` 幂等 upsert/删除投影行。
- IDOR 修复：所有按 id 读/改/删，先校验文档归属 `project_id`（见 §7）。
- 交付即回归：现有知识文档迁移后，AI 用例生成召回不变。

### 阶段 1 · 分类与检索
- 后端：目录树 CRUD（`/api/knowledge/folders`）、标签 CRUD、文档全文搜索（`GET /api/knowledge?q=&folder_id=&tag=`，PG `ILIKE` 起步，数据量大再上 `tsvector`+GIN）。
- 前端：左侧知识库**独立目录树**（替代模块树复用）+ 标签筛选 + 顶部**全文搜索框**，右侧文档列表。

### 阶段 2 · 阅读体验
- 全屏沉浸阅读页（路由 `/projects/:id/knowledge/:docId` 或大抽屉升级为全屏）。
- 正文自动生成**目录大纲(TOC)** + 标题锚点跳转；阅读宽度/字号切换。
- 列表从表格换成**卡片视图**（标题 + 摘要 + 标签 + 分类 + 更新时间 + 置顶标记），支持排序。
- 置顶/收藏；删除确认换成 shadcn `AlertDialog`（去掉原生 `confirm()`）。
- 版本历史 UI：查看/对比/回滚（表在阶段 0 已建）。

### 阶段 3 · 文件托管与预览
- 后端：附件上传 `POST /api/knowledge/{doc_id}/attachments`（校验类型/大小上限，默认 50MB）、下载/预览路由、删除附件。
- `doc_type='file'` 文档的创建流程（上传即建文档）。
- 前端：拖拽上传 + 预览组件（图片/PDF 原生，docx-preview/SheetJS 客户端渲染，兜底下载）。
- 可选：文件文本抽取喂 RAG（PDF 文本层 / docx 文本）。

### 阶段 4 · 导入导出
- 导出：单篇（MD / PDF）、整库/整目录（Zip，含富文本转 MD + 附件原件 + manifest.json）。
- 导入：上传 MD/Word 批量建文档（按文件名做标题，落到指定目录）。
- 后端复用阶段 3 的文件处理；PDF 导出走前端打印或轻量库。

### 贯穿项
- 作者/最后修改人：写操作记录 `author_id`/`editor_id`（用 `get_current_user`）。
- 版本快照：阶段 0 建表，阶段 2 出 UI，编辑保存时写快照。

## 7. 鉴权与安全

- 沿用平台约定：路由拿到 `project_id` 后调 `server.api.authz.assert_project_access(db, current_user, project_id)`（CLAUDE.md「对象级授权」段）。
- **IDOR 修复**：现有 `knowledge.py` 按 `doc_id` 直接改/删、不校验归属。新实现所有嵌套资源（文档→项目、附件→文档→项目）读改删前，都要验证归属链，不在各路由手写判断，走 authz 辅助函数。
- 文件下载：走鉴权路由校验项目访问权后再返回文件流（见 §3.2），不做公开静态挂载，避免目录裸奔泄露。
- 上传：白名单 MIME + 大小上限 + 文件名清洗（防路径穿越），落盘用 uuid 命名不用原始文件名。

## 8. 错误处理与不变量

- 服务层不碰 HTTP，路由层用 `db: DBDep` 注入 session（自动 commit/rollback/close），响应统一 `{status, data?, message?}`。
- RAG 投影同步失败**不应阻断**文档保存主流程：投影是派生数据，失败记日志、可后台补偿，用户的知识文档一定要存下来。
- 删除文档：级联删除其附件（含磁盘文件）、标签关联、版本、RAG 投影行。附件磁盘文件删除失败只记日志，不阻断 DB 事务。
- 迁移脚本 fail-closed 且幂等。

## 9. 测试与验收

平台无传统单测，验收以端到端 + 手动为主：
- 阶段 0：跑迁移脚本 → 现有知识文档全部出现在新表 → 用 `mcp__test-platform__run_tests` 触发一次会用到知识召回的 AI 生成，确认召回结果与迁移前一致。
- 各阶段前端：`npm run typecheck` + `npm run lint`（`--max-warnings 0`）+ `npm run build`（前端改动务必 build，否则 54351 看的是旧 dist，memory 记录）。
- 后端自查 `python -m compileall .`（无 ruff/black）。
- 改代码后记得杀旧 celery worker 再起（memory 记录）。

## 10. 文件改动清单（预估）

后端：
- 新增 `database/models/knowledge.py`（5 张表），`database/models/__init__.py` 导出。
- `database/models/project_context.py` 加 `knowledge_document_id` 列。
- Alembic 迁移 + `data_migrations/migrate_knowledge_to_dedicated_tables.py`。
- 重写 `server/services/knowledge_service.py`；新增目录/标签/附件/版本/导入导出的 service。
- 扩展 `server/api/knowledge.py`（拆分为文档/目录/标签/附件/导入导出多个路由或子模块）。
- 文件存储工具 `utils/knowledge_storage.py`（落盘/读取/删除）。

前端（`frontend/`）：
- 重构 `pages/knowledge/`：目录树、搜索、卡片列表、全屏阅读页、文件预览组件、导入导出入口、版本历史。
- `lib/api.ts` 补 folders/tags/attachments/search/import/export 接口。
- `types/domain.ts` 补类型。
- 新增依赖：`docx-preview`、`xlsx`（SheetJS），PDF 若用 pdf.js 亦加。
