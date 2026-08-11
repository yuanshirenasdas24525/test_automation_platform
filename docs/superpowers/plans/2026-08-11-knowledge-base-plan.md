# 知识库（Knowledge Base）实施文档 / 实施计划

- 配套需求文档：`docs/superpowers/specs/2026-08-11-knowledge-base-design.md`
- 方案：A（复用 `project_contexts`）
- 目标：v1 = 建库 + 入库（异步 embedding）；检索随复用零成本到位

## 0. 前置事实（已探明，落地即用）

- 上下文表：`database/models/project_context.py::ProjectContext`（已有 `project_id / module_id / source_type / title / content / summary / tags / keywords / embedding / importance / created_at / updated_at`）。
- 服务层：`server/services/context_service.py` → `save_contexts()` / `retrieve_context()` / `get_context_stats()`。
- embedding：`ai_gateway/embeddings.py::embed_texts`（config_group=`rag_embedding`，openai/ollama，Anthropic 无 embedding）。
- 向量列：`database/base.py` pgvector `Vector(dim)` 助手（PG + pgvector）。
- 去标签：`frontend/src/lib/utils.ts::stripHtml`（本分支刚抽出，前端复用）；后端另需一个 Python 版去标签。
- 富文本：`frontend/src/components/editor/RichTextEditor.tsx` / `RichTextViewer.tsx`。
- 路由注册：`server/api/<name>.py` → `server/api/__init__.py` 导出 → `server/main.py` 的 `for router in (...)` 循环（自动挂 `/api`）。

## 1. 后端

### 1.1 模型 & 常量（`database/models/project_context.py`）

- 加列：`content_html = Column(Text, nullable=True)`。
- 加常量：`CONTEXT_SOURCE_KNOWLEDGE = "knowledge"`。
- `to_dict()` 若存在，补 `content_html` 字段输出。

### 1.2 迁移

```bash
alembic revision --autogenerate -m "knowledge base: project_contexts.content_html"
# review 生成文件：确认只加 content_html 列，nullable，无误删
alembic upgrade head
```

### 1.3 Schema（`database/schemas/`）

- `KnowledgeDocCreate`：`{project_id:int, module_id:int|None, title:str, content_html:str, include_in_rag:bool=True}`
- `KnowledgeDocUpdate`：同上（除 project_id）
- `KnowledgeDocOut`：`{id, project_id, module_id, title, summary, include_in_rag, has_embedding, updated_at, created_at}`（列表用，不含大正文）
- `KnowledgeDocDetailOut`：`KnowledgeDocOut` + `content_html`

> `include_in_rag` 不是列，是派生：`include_in_rag = embedding is not None`（保存时决定是否算 embedding）。若要显式记录用户意图，可在 `tags`/`keywords` 里放标记，或后续加列；v1 用「有无 embedding」表达即可。

### 1.4 服务（`server/services/knowledge_service.py`，薄封装复用 context_service）

- `list_docs(db, project_id, module_id=None) -> list[ProjectContext]`：filter `source_type==KNOWLEDGE`（+ module_id）。
- `get_doc(db, doc_id) -> ProjectContext`。
- `create_doc(db, payload) -> ProjectContext`：
  1. `content = html_to_text(payload.content_html)`（新增 Python 去标签工具，见 1.6）
  2. 建 `ProjectContext(source_type=KNOWLEDGE, context_type=CONTEXT_TYPE_TERM_DEFINITION 或 'requirement' 缺省, title, content, content_html, module_id, project_id, importance=3)`
  3. 若 `include_in_rag`：提交 embedding 异步任务（见 1.5）
- `update_doc(...)`：同 create，重算 content / 重新入库；关掉 RAG 时清 `embedding=None`。
- `delete_doc(...)`：删行。

> embedding 计算复用 `context_service` 内既有逻辑：若 `save_contexts` 内部已算 embedding，则直接调 `save_contexts([{...}], project_id, source_type='knowledge')` 拿到 id 后回填 `content_html/module_id`；否则显式调 `ai_gateway.embed_texts`。实施时先读 `save_contexts` 全文确认它是否算 embedding，就近复用，避免重复实现。

### 1.5 异步入库（`tasks/`）

- 复用现有 celery 模式：新增 `tasks/knowledge_index_task.py::index_knowledge_doc_task(doc_id)`，或若已有通用 embedding 任务则复用。
- 任务内：取文档 `content` → `embed_texts` → 写回 `project_contexts.embedding`。失败不抛给用户，记日志 + 状态可留待索引。
- `celery_app.py` 底部 `import tasks.knowledge_index_task  # noqa: F401`。
- EAGER 模式（`CELERY_TASK_ALWAYS_EAGER=1`）下同步跑，便于本地验证。

### 1.6 后端去标签工具

- `content` 需纯文本。新增 `server/services/html_text.py::html_to_text(html)`（正则去标签 + 反转义 + 压空白），或复用 `doc_parser` 里若已有的清洗函数（先 grep 确认）。

### 1.7 路由（`server/api/knowledge.py`）

- `router = APIRouter(prefix="/knowledge", tags=["knowledge"])`
- 5 个端点见需求文档 §6；每个先 `assert_project_access(db, current_user, project_id)`。
- `server/api/__init__.py` 加 `from .knowledge import router as knowledge_router` + 导出；`server/main.py` 的 router 循环里加 `knowledge_router`。

## 2. 前端

### 2.1 类型（`types/domain.ts`）

```ts
export interface KnowledgeDoc {
  id: number; project_id: number; module_id: number | null;
  title: string; summary?: string | null; content_html?: string | null;
  include_in_rag: boolean; has_embedding?: boolean;
  created_at?: string | null; updated_at?: string | null;
}
```

### 2.2 API（`lib/api.ts` → `knowledgeApi`，照 `requirementsApi`）

- `list(projectId, {module_id?})` / `get(id)` / `create(payload)` / `update(id, payload)` / `remove(id)`。

### 2.3 组件

- `pages/knowledge/KnowledgeBasePanel.tsx`：
  - `useQuery(["knowledge", projectId, moduleId], () => knowledgeApi.list(...))`
  - 列表：标题 + `stripHtml(summary/content)` 预览（`line-clamp`）+ 更新时间 + 「AI 知识库」徽标（`has_embedding` 绿，关闭灰）。
  - 顶部「新建文档」按钮（带入当前 `selectedModuleId`）。
- `pages/knowledge/KnowledgeDocDialog.tsx`：`RichTextEditor` + 模块选择（复用现有模块 picker）+ `include_in_rag` Switch（默认开）+ 保存/删除。`react-hook-form` + `zod`，`sonner` toast，成功后 `invalidateQueries(["knowledge", projectId])`。

### 2.4 接入 `ProjectManagementPage.tsx`

- import `BookOpen`（lucide）、`KnowledgeBasePanel`。
- `TabsList` 在「需求池」`TabsTrigger` 之后插：
  `<TabsTrigger value="knowledge"><BookOpen className="h-4 w-4 mr-1" />知识库</TabsTrigger>`
- 内容区加 `{activeTab === "knowledge" && (<div className="flex-1 overflow-y-auto p-6"><KnowledgeBasePanel projectId={projectId} selectedModuleId={selectedModuleId} /></div>)}`
- 左侧模块树对 `knowledge` tab 同样生效（已有的 `selectedModuleId` 逻辑无需改，只要该 tab 也读它）。

## 3. 验证（无传统单测，端到端 + 手测）

1. `python -m compileall server tasks database` 过编译。
2. `npm run typecheck` + `npm run lint`（`--max-warnings 0`）。
3. EAGER 起后端，`POST /api/knowledge` 建文档 → 查 `project_contexts` 出现 `source_type='knowledge'` 行且 `embedding` 非空。
4. 前端硬刷新（54351 走本分支 Stop hook 自动 build）→ 知识库 tab 建/改/删、模块过滤、预览纯文本。
5. `context_service.get_context_stats(project_id)` 计数增加。
6. 关闭 RAG 开关保存 → `embedding` 清空、人读内容仍在。
7. 越权：换无权 project_id 调接口被 403。

## 4. 实施顺序（建议）

1. 模型加列 + 常量 + 迁移（先让库结构就位）
2. 后端去标签工具 + service + 异步任务 + 路由 + 注册
3. `python -m compileall` + EAGER 手测后端闭环
4. 前端 types + api + 两个组件 + tab 接入
5. typecheck / lint + 前端手测
6. 提交

## 5. 边界回顾（不做）

版本历史、评论、逐文档权限、附件内嵌、跨项目共享、全文搜索、手动重索引——全部留待后续迭代（见需求文档 §4.2）。
```
