# 知识库（Knowledge Base）需求文档

- 日期：2026-08-11
- 分支：`claude/requirement-list-tags-sorting-6924dc`
- 状态：已评审（方案 A，v1 范围已锁）

## 1. 背景与目标

项目管理页现有 tab：`需求池 / 版本迭代 / 项目概览 / 项目配置 / 脚本库`。缺一个沉淀「项目知识」的地方——接口约定、测试规范、环境说明、业务术语等，目前散落在需求描述、聊天、外部文档里，既不便人查阅，也没被 AI 用例生成复用。

**目标**：在「需求池」后面新增「知识库」tab，让团队以富文本文档形式沉淀项目知识；文档**既供人阅读**，又**自动进入 RAG**，成为 AI 生成用例/需求时的知识来源。

## 2. 关键决策（已确认）

| 决策点 | 结论 |
|---|---|
| 定位 | 富文本文档库，**人读 + 喂 AI（RAG）两者都要** |
| 导航 | **复用左侧需求模块树**，文档挂到模块下（`module_id` 可空=根级） |
| 编辑器 | 复用现有 `RichTextEditor`（富文本，所见即所得） |
| 存储落点 | **方案 A：直接进 `project_contexts`**，新增 `content_html` 列，`source_type='knowledge'` 区分 |
| RAG 检索 | 因用例生成已消费 `project_contexts`，入库即被召回——检索**零成本随 v1 到位** |

### 为什么是方案 A（复用 `project_contexts`）

平台已有一套喂 AI 用例生成的上下文 RAG：`project_contexts` 表 + `context_service`（`save_contexts` / `retrieve_context` / `get_context_stats`）+ `ai_gateway.embed_texts`（embedding 能力已就绪，config 驱动 openai/ollama）+ `database/base.py` 的 pgvector `Vector(dim)` 助手。用例生成侧（`case_generation_context_builder`、`context_service.retrieve_context` 等）**已经在读** `project_contexts`。

因此知识库不另起平行向量表，而是复用这套设施：知识文档就是一条 `source_type='knowledge'` 的 `project_contexts` 记录。人看新增的 `content_html`，RAG 用既有的 `content`（纯文本）。改动面最小，且知识入库后 AI 自动可召回。

## 3. 用户故事

- **作为测试/开发**，我能在项目下按模块建立富文本知识文档（如「登录模块接口约定」），供团队查阅。
- **作为团队成员**，我能在左侧模块树点某模块，只看该模块下的知识文档；点根级看全部。
- **作为 AI 用例生成的使用者**，我沉淀的知识会自动进入 AI 的检索知识源，让生成的用例更贴合本项目约定——无需额外操作。
- **作为文档维护者**，我能编辑、删除文档；关掉「纳入 AI 知识库」开关时该文档只给人看、不进 RAG。

## 4. 功能范围（v1）

### 4.1 必做

1. **新增「知识库」tab**：插在「需求池」之后，`value='knowledge'`，图标 `BookOpen`（lucide）。
2. **左侧模块树复用**：知识库 tab 激活时，沿用页面已有的模块树与 `selectedModuleId` 过滤逻辑（与需求池同款交互）。
3. **文档列表**：显示当前项目（+可选模块过滤）下 `source_type='knowledge'` 的文档：标题、纯文本摘要预览（去标签，复用已抽出的 `stripHtml`）、更新时间、「AI 知识库」状态徽标。
4. **新建 / 编辑文档**：抽屉或对话框内用 `RichTextEditor` 编辑；字段 = 标题、所属模块（默认取当前 `selectedModuleId`）、正文富文本、`纳入 AI 知识库` 开关（默认开）。
5. **删除文档**：二次确认。
6. **入库（embedding）**：保存文档且开关开启时，把正文纯文本经 `context_service` 逻辑算 embedding 并落 `project_contexts`；关闭开关则清掉/不建 embedding（仍保留人读内容）。入库走**异步**，不阻塞保存返回。

### 4.2 v1 明确不做（YAGNI）

- 文档版本历史 / 回滚
- 评论、@提及、协同编辑
- 逐文档权限（沿用现「成员皆可访问项目」，走 `assert_project_access`）
- 文档内附件上传 / 内嵌大文件
- 跨项目共享知识
- 知识库全文搜索框（v1 靠模块树 + 列表；搜索留下一迭代）
- 手动「重新索引」按钮（保存即索引，够用）

## 5. 数据模型（方案 A 增量）

在既有 `project_contexts` 上**增量**，不新建表：

| 变更 | 说明 |
|---|---|
| 新增列 `content_html TEXT NULL` | 富文本原文，供人阅读/编辑；仅 `source_type='knowledge'` 行使用 |
| 新增来源常量 `CONTEXT_SOURCE_KNOWLEDGE = "knowledge"` | 区分「人工知识文档」与「AI 抽取上下文」 |
| 复用 `content TEXT` | 去标签纯文本，供 RAG 分块/embedding 与摘要 |
| 复用 `module_id`（已存在，可空） | 挂到需求模块树 |
| 复用 `title / summary / tags / keywords / importance / embedding / created_at / updated_at` | 全部已存在 |

> 迁移需 `alembic revision --autogenerate` 后**人工 review**（autogenerate 常漏 server_default/index）。`content_html` 无默认、可空，加列安全。

## 6. 接口（REST，`/api/knowledge`）

统一响应信封 `{status, data?, message?}`，`db: DBDep` 注入，路由内先 `assert_project_access`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/knowledge?project_id=&module_id=` | 列文档（`source_type='knowledge'`，可按模块过滤），返回列表（不含大正文，含摘要） |
| GET | `/api/knowledge/{id}` | 取单篇（含 `content_html`） |
| POST | `/api/knowledge` | 建文档：`{project_id, module_id?, title, content_html, include_in_rag}` |
| PUT | `/api/knowledge/{id}` | 改文档（同上字段） |
| DELETE | `/api/knowledge/{id}` | 删文档 |

保存/更新时后端：`content = stripHtml(content_html)`（服务端去标签）→ 若 `include_in_rag` 异步 embedding 落库。

## 7. 前端结构

- `ProjectManagementPage.tsx`：加 `TabsTrigger value="knowledge"`；`activeTab==='knowledge'` 渲染 `<KnowledgeBasePanel projectId selectedModuleId />`。
- 新增 `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx`：文档列表 + 新建按钮。
- 新增 `frontend/src/pages/knowledge/KnowledgeDocDialog.tsx`（或抽屉）：`RichTextEditor` 编辑 + 模块选择 + RAG 开关。
- `lib/api.ts` 加 `knowledgeApi`（照 `requirementsApi` 形态）。
- `types/domain.ts` 加 `KnowledgeDoc` 类型。
- 列表预览复用 `stripHtml`（已在 `lib/utils.ts`）。

## 8. 成功标准

1. 能在知识库 tab 下按模块建/改/删富文本文档，刷新后持久。
2. 列表预览是**纯文本**（不漏 HTML 标签），和需求池预览一致。
3. 开着「纳入 AI 知识库」保存后，`project_contexts` 出现对应 `source_type='knowledge'` 行且带 embedding；`context_service.get_context_stats` 计数增加。
4. 关掉开关的文档不产生/清除 embedding，但人读内容仍在。
5. 不越权：换一个无权项目 id 调接口被 `assert_project_access` 拦。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| embedding 未配置（rag_embedding group 空）时保存报错 | 入库异步 + 容错：embedding 失败不阻塞保存，标记待索引，记日志（沿用 `embeddings.py` 的降级） |
| `project_contexts` 混入两类来源导致检索被知识文档「稀释」 | 用 `importance` 与 `source_type`/`context_type` 控权重；retrieve 侧后续可加 `target_types` 过滤 |
| 富文本 XSS | 展示侧用现有 `RichTextViewer`（已做 sanitize，参照 `TaskDetailPage` 的 DOMPurify 路径） |
```
