# AI 需求分析重设计 · M6 实施文档

> commit 前缀：`feat(ai-m6):`
> 计划镜像：`~/.claude/plans/docs-tapd-tapd-generic-wombat.md`

## Context

当前 AI 需求分析（`pages/RequirementsPage.tsx` 的 `AiParseDialog` + `tasks/ai_tasks.py::_handle_requirement_parse`）的输入方式是粘贴文本 / 上传单文件，产物是落在 `ai_runs.output_payload` 里的一个 JSON。这种方式存在三个问题：

1. **割裂**：分析对象是"一段文本"，不是"系统里的某个需求"，PM 要先复制需求标题/描述/链接才能让 AI 分析，违背"以需求为中心"的 M5 设计目标。
2. **上下文不全**：忽略了 M5 已建好的关联关系（关联模块、依赖需求、子需求、附件），AI 看到的信息片面。
3. **结果一次性**：产物是 JSON 不是 MD，导出/编辑/版本对比都做不到，PM 没法在产物上继续打磨。

本次重设计目标：让 AI 分析变成"以需求 ID 为输入 → 平台自动汇聚上下文 → 产出可编辑的 MD 文档 → 文档化保存 + git-like 版本历史"的闭环。同时把模型选择搬到配置中心，支持多模型并列。

## Scope（Out of M6）

- 实时协同编辑（OT/CRDT）
- 跨需求的"全局知识库"上下文（只读当前需求的强关联节点）
- 自动定时重新分析 / webhook 触发
- 自定义 prompt 的版本管理（只允许 inline 补充）

---

## 已锁定的设计决策

| # | 主题 | 决策 |
|---|---|---|
| 1 | 分析文档存储模型 | **每次分析 = 独立文档**。一个需求可拥有 N 份分析文档，每份有自己的 git-like 编辑历史。需求列表入口 → 弹文档列表 → 选其一进入查看/编辑 |
| 2 | 编辑历史粒度 | 每次"保存"= 一条 version 行（存 markdown 全文 + 作者 + 时间 + 提交说明），diff 在前端用 `diff-match-patch` 实时计算 |
| 3 | 图片附件 | **Vision API 优先 + OCR 回退**。模型支持 vision（gpt-4o / claude-3.x / qwen-vl）→ 直接 base64 喂入；不支持 → 走 pytesseract OCR 提文字；都失败 → 标注"图片 N 张未解析"。`AiModelConfig` 加 `supports_vision: bool` |
| 4 | 多模型策略 | **单模型默认 + 多模型并列**。配置中心可保存多套 model config；分析时下拉默认选 1 个，高级里支持勾选 N 个并行 → 后端为每个 model 独立创建一份 `AnalysisDocument`，不再 vote-merge |
| 5 | 文档存储位置 | DB 表 `requirement_analysis_documents` 存 markdown 全文，不落盘；导出按钮即时把 markdown 串成 `.md` 下载 |
| 6 | 附件读取范围 | `kind=link` 不抓取（避免 SSRF + 速率）；只读 `kind=file` 的本地附件；白名单：PDF / DOCX / MD / TXT + 图片（PNG/JPG/JPEG/WEBP） |
| 7 | 自定义 prompt | 分析触发时 textarea 字段，明文存 `ai_runs.input_payload.user_prompt`；prompt 模板走 `ai_gateway/prompts/requirement_analysis_v2.md` |
| 8 | 配置中心 AI 页 | 表单 CRUD：每条 row = 1 个 `AiModelConfig`（name / provider / model / base_url / api_key / supports_vision / is_default / enabled）。存到 `config_store` 的 `(group="ai_models", key=name)` 下，value=JSON |

---

## 1. 数据层改造

### 1.1 Alembic 迁移 `m6_0001_analysis_documents.py`

**新表 `requirement_analysis_documents`：**

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `requirement_id` | int FK requirements.id ON DELETE CASCADE | |
| `ai_run_id` | int FK ai_runs.id nullable | 关联首次生成的 run；后续手工编辑不动 |
| `title` | str(200) | 默认 `f"AI 分析 - {model_name} - {YYYYMMDD HH:mm}"`，可手改 |
| `current_markdown` | Text | 当前最新 markdown |
| `current_version` | int default 1 | 当前版本号 |
| `model_label` | str(100) | 生成时用的模型展示名（e.g. "openai / gpt-4o"） |
| `created_by_id` | int FK users.id | |
| `created_at`, `updated_at` | datetime | |

索引：`(requirement_id, created_at desc)`、`ai_run_id`

**新表 `requirement_analysis_versions`：**

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `document_id` | int FK requirement_analysis_documents.id ON DELETE CASCADE | |
| `version_no` | int | 1-based，按 document_id 分组自增 |
| `markdown` | Text | 该版本的全量快照 |
| `change_summary` | str(500) nullable | 用户填的"提交说明" |
| `author_id` | int FK users.id | |
| `is_ai_generated` | bool default False | True=AI 产出；False=人工编辑 |
| `created_at` | datetime | |

索引：`(document_id, version_no)` 唯一约束

### 1.2 `AiModelConfig` Pydantic schema `database/schemas/ai_config.py`

不新建表，**用 `config_store(config_group="ai_models", config_key=<name>)` 行存 JSON 值**。

```python
class AiModelConfig(BaseModel):
    name: str                       # 用户起的别名，唯一
    provider: str                   # openai / anthropic / ollama / deepseek / azure / custom
    model: str                      # gpt-4o / claude-3.7-sonnet / qwen2.5:7b
    base_url: str | None = None
    api_key: str | None = None      # 可空（本地 ollama 不需要）
    supports_vision: bool = False
    is_default: bool = False
    enabled: bool = True
    extra: dict[str, Any] = {}      # 透传 provider 的额外参数（temperature 等）
```

---

## 2. 后端 API

### 2.1 AI Model Config CRUD `server/api/ai_models.py`（新文件）

```
GET    /api/ai-models                 → list AiModelConfig
POST   /api/ai-models                 → create
PUT    /api/ai-models/{name}          → update
DELETE /api/ai-models/{name}          → delete
POST   /api/ai-models/{name}/test     → ping 测试连通性，返回 { ok, latency_ms, sample }
```

Service 层 `server/services/ai_model_service.py` 封装 `list_ai_models()` / `upsert_ai_model()` / `delete_ai_model()`，底层走 `config_store`。

### 2.2 分析文档 CRUD `server/api/requirement_analysis.py`（新文件）

```
GET    /api/requirements/{rid}/analysis-documents               → 列文档
POST   /api/requirements/{rid}/analysis-documents               → 触发 AI 分析
                                                                  body: { model_names: [str], user_prompt?: str }
                                                                  返回 { runs: [{ run_id, document_id, model_name }] }
GET    /api/analysis-documents/{id}                             → 详情 + 当前 markdown
PUT    /api/analysis-documents/{id}                             → 编辑保存（body: { markdown, change_summary? }）→ 新增 version
DELETE /api/analysis-documents/{id}                             → 删文档（级联删 versions）

GET    /api/analysis-documents/{id}/versions                    → 列版本（不带 markdown）
GET    /api/analysis-documents/{id}/versions/{version_no}       → 单版本（带 markdown）
GET    /api/analysis-documents/{id}/versions/{a}/diff/{b}       → 返回两版本 markdown，前端算 diff
GET    /api/analysis-documents/{id}/export                      → 直接返回 markdown，header attachment
```

权限：当前会话 user 必须是需求所在 project 的成员（沿用 `server/api/deps.py`）。

### 2.3 上下文构建器 `server/services/requirement_context_builder.py`（新文件）

```python
def build_requirement_context(session, requirement_id: int) -> RequirementContext:
    """
    返回结构：
    {
      "requirement": {id, title, description, priority, system_status,
                      planned_start_at, planned_end_at, assignees: {dev/test/pm/ui: [names]}},
      "module": {id, name, description, related_features: [feature.name]} | None,
      "depends_on": [{id, title, description}, ...],
      "children": [{id, title, description, priority}, ...],
      "attachments": {
          "documents": [{name, mime, text_excerpt: str (最长 4000 字)}],
          "images":    [{name, mime, abs_path: str}],   # base64 由 gateway 按需现填
          "skipped":   [{name, reason: "link" | "unsupported_type"}],
      },
    }
    """
```

附件细则：
- 走 `Attachment.kind == "file"`，路径基于 `data/attachments/req_{rid}/`
- 文档类型用 `server/services/doc_parser.py` 现有解析器（PDF/DOCX/MD/TXT），每文件截断 4000 字
- 图片：只记录 path，不读 base64
- `depends_on` 在 `Requirement` 是 JSON 数组（M5 已建），批量 `WHERE id IN (...)`
- `children` 走 `selectinload(Requirement.children)` 预加载

### 2.4 分析任务改造 `tasks/ai_tasks.py::_handle_requirement_analyze`（新 handler）

注册到 `_HANDLERS`；老的 `_handle_requirement_parse` 保留兼容（M7 删）。

流程：

```
1. 读 ai_runs 行 → input_payload = {requirement_id, model_name, user_prompt?}
2. context = build_requirement_context(session, requirement_id)
3. 加载 prompt 模板 ai_gateway/prompts/requirement_analysis_v2.md（新文件）
4. 替换 {{REQUIREMENT_TITLE}} / {{REQUIREMENT_DESCRIPTION}} / {{MODULE_INFO}} /
       {{DEPENDS_ON}} / {{CHILDREN}} / {{DOCUMENT_EXCERPTS}} / {{USER_PROMPT}} 等占位
5. 取 model_config from config_store
6. 若 supports_vision and context.attachments.images:
       gateway.chat_with_images(prompt, images, model_config)
   else:
       若有 images → 先逐个 OCR 提文，把 OCR 文字拼进 {{OCR_EXCERPTS}}
       再走 gateway.chat(prompt, model_config) 纯文本
7. 拿到 markdown → 创建 RequirementAnalysisDocument(current_markdown=md, current_version=1)
        + 同时插一条 RequirementAnalysisVersion(version_no=1, is_ai_generated=True)
8. 更新 ai_runs.output_payload = {document_id, summary: 前 200 字}
9. run.status = "succeeded"
```

**多模型并行：** API 层拿到 `model_names: [...]` 后为每个 model 独立创建 1 个 `ai_run`，每个 run 独立产出 1 份 document。不在后端 vote-merge。

### 2.5 Gateway 扩展 `ai_gateway/gateway.py`

新增 `chat_with_images(prompt: str, images: list[dict], model_config) -> str`：
- OpenAI / Azure: `messages=[{"role":"user","content":[{"type":"text",...},{"type":"image_url","image_url":{"url":"data:..."}}]}]`
- Anthropic: `content=[{"type":"image","source":{"type":"base64",...}},{"type":"text","text":...}]`
- Ollama: `messages=[{...,"images":[base64_list]}]`（llava 系列）
- 其他: raise `ProviderDoesNotSupportVisionError` → 上层走 OCR 分支

新增 `ocr_extract(image_path: str) -> str`：薄封装 pytesseract，失败返回空串。

### 2.6 依赖

`requirements.txt`：
- `pytesseract>=0.3.10`
- `Pillow>=10.0.0`

`Dockerfile`：`RUN apt-get install -y tesseract-ocr tesseract-ocr-chi-sim`（支持中文截图）

---

## 3. 前端改造

### 3.1 依赖

```bash
npm i diff-match-patch
npm i -D @types/diff-match-patch
```

### 3.2 API 层 `frontend/src/lib/api.ts`

```typescript
export const aiModelsApi = {
  list: () => request<AiModelConfig[]>('/api/ai-models'),
  upsert: (cfg: AiModelConfig) => ...,
  delete: (name: string) => ...,
  test: (name: string) => request<{ ok: boolean; latency_ms: number; sample: string }>(...),
};

export const analysisDocsApi = {
  listByRequirement: (rid: number) => ...,
  trigger: (rid: number, body: { model_names: string[]; user_prompt?: string }) => ...,
  get: (id: number) => ...,
  save: (id: number, body: { markdown: string; change_summary?: string }) => ...,
  delete: (id: number) => ...,
  listVersions: (id: number) => ...,
  getVersion: (id: number, v: number) => ...,
  export: (id: number) => fetch + blob download,
};
```

### 3.3 RequirementsPage 行内入口

替换现有 `AiParseDialog` 触发方式：

1. 每条 Requirement 行操作列加 `Bot` 按钮 → 打开 `AiAnalysisLauncherDialog`
2. 行尾加 `FileText` 按钮 → 打开 `AnalysisDocumentListDialog`（若有分析文档则按钮亮起 + 徽标数量）

### 3.4 新组件

| 组件 | 路径 | 职责 |
|---|---|---|
| `AiAnalysisLauncherDialog.tsx` | `pages/requirements/dialogs/` | 触发分析。表单：model picker（默认单选 + 多选高级）+ user_prompt textarea + "开始分析" → POST trigger → toast + 自动打开 list |
| `AnalysisDocumentListDialog.tsx` | 同上 | 列该需求所有 docs（title / model_label / created_at / actions），点击行打开 viewer |
| `AnalysisDocumentViewerDialog.tsx` | `pages/requirements/viewers/` | 主组件：左侧 toolbar（编辑 / 保存 / 导出 / 删除 / 历史）+ 中间 MarkdownView/Editor 切换 + 右侧抽屉版本列表 |
| `VersionDiffViewer.tsx` | `components/diff/` | 接收 `before, after` markdown，`diff-match-patch` 计算 + 自渲染 `<ins>` / `<del>` 着色 |
| `AiModelConfigTab.tsx` | `pages/config/tabs/` | 替换 ConfigPage 的 ai tab，完整 CRUD + 测试连通性 |

#### Viewer 内部状态

```
mode: "view" | "edit"
draft: string                       // edit 模式本地 markdown
selectedVersion: number | null      // 右侧抽屉选中的版本（null=最新）
```

- `view`：渲染 `current_markdown`（或选中 version）+ 编辑按钮可见
- `edit`：MarkdownEditor 受控；"保存"按钮 → 小 input 填 `change_summary` → PUT
- "历史"→ 抽屉列所有 version（`v3 · 2026-05-12 14:32 · 张三 · "调整描述格式"`），选 2 个 → 弹 VersionDiffViewer

### 3.5 ConfigPage AI tab

现有 ai tab 替换为 `<AiModelConfigTab />`：
- 表格列出所有 `AiModelConfig`（name / provider / model / vision 标记 / is_default / 操作）
- "新增模型" → Dialog 表单（字段见 §1.2）
- 每行尾"测试"按钮 → `POST /api/ai-models/{name}/test`，结果 toast

### 3.6 类型 `frontend/src/types/domain.ts`

```typescript
export interface AiModelConfig { name; provider; model; base_url?; supports_vision; is_default; enabled; extra; }
export interface AnalysisDocument { id; requirement_id; ai_run_id?; title; current_markdown; current_version; model_label; created_by_id; created_at; updated_at; }
export interface AnalysisVersion { id; document_id; version_no; markdown?; change_summary?; author_id; is_ai_generated; created_at; }
```

---

## 4. 任务拆分（commit 前缀 `feat(ai-m6):`）

| # | Task | 类型 | 关键文件 |
|---|---|---|---|
| 1 | Alembic 迁移：分析文档 + 版本表 | backend schema | `database/migrations/versions/m6_0001_*.py`、`database/models/requirement_analysis_document.py`、`requirement_analysis_version.py` |
| 2 | AiModelConfig schema + service | backend | `database/schemas/ai_config.py`、`server/services/ai_model_service.py` |
| 3 | AI model CRUD API | backend | `server/api/ai_models.py`、`server/api/__init__.py`、`server/main.py` |
| 4 | 上下文构建器 service | backend | `server/services/requirement_context_builder.py` |
| 5 | Gateway 加 chat_with_images + ocr_extract | backend | `ai_gateway/gateway.py`、`requirements.txt`、`Dockerfile` |
| 6 | Prompt 模板 v2 | backend | `ai_gateway/prompts/requirement_analysis_v2.md` |
| 7 | 分析 handler 改造 | backend | `tasks/ai_tasks.py` |
| 8 | 分析文档 CRUD + 版本 API | backend | `server/api/requirement_analysis.py`、deps、main |
| 9 | 前端 api.ts 扩展 + types | frontend | `frontend/src/lib/api.ts`、`types/domain.ts` |
| 10 | AiAnalysisLauncherDialog | frontend | `pages/requirements/dialogs/` |
| 11 | AnalysisDocumentListDialog | frontend | 同上 |
| 12 | AnalysisDocumentViewerDialog | frontend | `pages/requirements/viewers/` |
| 13 | VersionDiffViewer | frontend | `components/diff/` |
| 14 | RequirementsPage 行操作列接入 | frontend | `pages/RequirementsPage.tsx` |
| 15 | AiModelConfigTab | frontend | `pages/config/tabs/AiModelConfigTab.tsx`、`pages/ConfigPage.tsx` |

---

## 5. 关键复用点

- **ai_gateway/gateway.py** provider 路由（`_provider_chat` / `_call_openai` 等）→ 加 vision 分支
- **config_store 表 + 现有 CRUD pattern** → `AiModelConfig` 不新建表
- **server/services/doc_parser.py** PDF/DOCX/MD/TXT 解析器（已支持）→ context builder 直接调用
- **database/models/attachment.py** kind/url/file 已 M5 建好
- **frontend/src/components/editor/MarkdownEditor.tsx & MarkdownView.tsx** M5 已建 → 文档查看 + 编辑直接复用
- **ConfigPage.tsx** ai tab 已有壳子，只换内容
- **ai_runs.input_payload / output_payload / status** → 不动 schema，新 handler 复用
- **server/api/deps.py** 项目成员校验

## 6. 必须新建

- 2 个 DB 表 + 模型类（analysis_documents / analysis_versions）
- 上下文 builder service
- vision + OCR 双路径在 gateway
- 前端 4 个新 Dialog/Viewer + 1 个 diff 渲染组件
- ConfigPage ai tab 实质内容

## 7. 风险 & 待定

- **pytesseract + chi-sim 让 docker 镜像膨胀 ~150 MB**：可接受，PM 截图大多是中文 UI 图
- **diff-match-patch 在大文档（>50 KB）上的渲染性能**：MD 分析文档通常 < 10 KB；超阈值切到行级 diff（unified 风格）
- **AiModelConfig.api_key 明文存 config_store**：跟现有 sensitive config 同样处理，是当前平台的事实标准；加密留 M7
- **prompt 超 token 上限**：context builder 对每段做截断（标题 100 字、描述 500 字、附件 4000 字），prompt 末尾输出"[已截断 N 项]"提示模型
- **多模型并行其中一个失败**：其他 run 不受影响；失败的 `ai_run.status="failed"`，前端列表带感叹号

---

## 8. Verification

### 后端 smoke

```bash
# 1. 配 1 个 OpenAI 模型 + 1 个 Ollama 本地模型
curl -X POST http://127.0.0.1:54351/api/ai-models -H 'Content-Type: application/json' \
  -d '{"name":"gpt-4o","provider":"openai","model":"gpt-4o","api_key":"sk-...","supports_vision":true,"is_default":true,"enabled":true}'

curl -X POST http://127.0.0.1:54351/api/ai-models \
  -d '{"name":"llama3-local","provider":"ollama","model":"llama3.1:8b","base_url":"http://127.0.0.1:11434","enabled":true}'

# 2. 测试连通性
curl -X POST http://127.0.0.1:54351/api/ai-models/gpt-4o/test

# 3. 准备 1 个有附件 + 子需求 + depends_on 的需求（用 M5 API 建）

# 4. 触发分析
curl -X POST http://127.0.0.1:54351/api/requirements/42/analysis-documents \
  -d '{"model_names":["gpt-4o","llama3-local"], "user_prompt":"重点关注性能与异常路径"}'
# 期望返回 2 个 run + 2 个 document_id

# 5. 列文档
curl http://127.0.0.1:54351/api/requirements/42/analysis-documents | jq

# 6. 编辑保存
curl -X PUT http://127.0.0.1:54351/api/analysis-documents/1 \
  -d '{"markdown":"# 新的标题\n...", "change_summary":"调整结构"}'

# 7. 列版本 + diff
curl http://127.0.0.1:54351/api/analysis-documents/1/versions | jq
curl http://127.0.0.1:54351/api/analysis-documents/1/versions/1/diff/2 | jq

# 8. 导出
curl http://127.0.0.1:54351/api/analysis-documents/1/export -o analysis.md

# 9. 删除
curl -X DELETE http://127.0.0.1:54351/api/analysis-documents/1
```

### 前端验证（浏览器手测）

1. `/config` → AI 标签 → 新增 2 个模型 → 各自"测试连通性"通过 → 一个设为默认
2. `/projects/1/requirements` → 选有附件/子需求的需求 → 点 Bot 按钮 → Dialog 默认选默认模型 → 高级里勾另一个 → 填补充 prompt → 提交
3. 等几秒 → 行内 FileText 按钮亮起，徽标 = 2
4. 点 FileText → list dialog 列出 2 份文档 → 进入其中一份 viewer
5. markdown 渲染 → "导出"下载 .md 文件正确
6. "编辑" → 改两段 → "保存"弹 input 填提交说明 → 列表里 version 数 +1
7. "历史" → 选 v1 / v2 → diff 视图正确高亮新增/删除段落
8. 删除文档 → 列表少 1 份；另一份仍在

### 联动验证

- 需求挂 1 张 PNG 截图（中文 UI）+ 1 个 PDF
  - 用 `gpt-4o`（vision）跑 → 日志确认图被 base64 喂入
  - 用 `llama3-local`（无 vision）跑 → 日志确认走 OCR 分支，OCR 文字拼进 prompt
- model 设 `enabled=false` → launcher dialog 里该 model 消失
- ai_run.status 失败时 → list dialog 仍能看到失败 run 的 error_message

每步通过：

```bash
cd frontend && npm run typecheck
python -m compileall server tasks database ai_gateway runners
```
