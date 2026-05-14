我
# AI 测试用例一键生成 · M7 实施文档

> commit 前缀：`feat(ai-m7):`
> 计划镜像：`~/.claude/plans/md-ai-dynamic-dove.md`
> 前置里程碑：M5（PM 工作流 / 需求模型 / 模块树）、M6（AI 需求分析文档 + 多模型 + AiModelConfig）
> 宏观蓝图来源：`docs/ai_features_requirements.md` 功能 4「AI 生成功能用例」

## Context

M6 把 AI 需求分析做成了"以需求 ID 为输入 → 平台汇聚上下文 → 产出可编辑的 MD 文档 + git-like 版本历史"的闭环。但产物到此为止 —— PM 拿到漂亮的分析文档之后，仍然要**手工对照文档把用例一条一条敲进 `test_cases` 表**。这违背了 M5 / M6 的"以需求为中心、AI 减负"的初衷，也让分析文档变成了一份"看完就锁起来"的死稿。

当前代码现状（已 grep 确认）：

- `tasks/ai_tasks.py` 的 `_HANDLERS` 只注册了 `requirement_parse / requirement_analyze / test_plan`，**完全没有任何用例生成 handler**
- `server/api/` 下没有 `case_generation` / `case_drafts` 相关路由
- `database/models/test_case.py` 与 `test_step.py` 是空壳 —— `test_cases` 表存在但项目里几乎没有自动化生成的用例
- `frontend/src/pages/requirements/` 有 M6 的 `AiAnalysisLauncherDialog` / `AnalysisDocumentViewerDialog`，但没有用例生成相关组件

M7 的目标是把这三个痛点一次性合上：

1. **分析→用例断层**：分析文档 Viewer 里加"生成用例"按钮；需求行操作列加"AI 生成用例"按钮；需求列表批量勾选后批量生成。**三入口并存**。
2. **AI 产出污染主表**：所有 AI 产出先进**草稿表 `ai_case_drafts`**，状态 `pending`；PM 在前端预览/编辑/勾选后，**批量 commit** 才写入 `test_cases`。
3. **后续自动化扩展不返工**：草稿表带 `step_template` JSON 字段，M8 接 OpenAPI / 录制后可直接据此生成自动化 step。

最终闭环：

```
[需求列表] / [需求行] / [分析文档 Viewer]
   ↓ 点击"AI 生成用例"
[CaseGenerationLauncherDialog]
   - 选 model / 数量 / 场景配比 / 自定义 prompt
   - 可选：勾选该需求已有的 UI 截图附件 / 即时上传 PNG 截图
   ↓ POST /api/ai/case-generation
[ai_runs(status=running)]  →  tasks/ai_tasks::_handle_case_generation
   ↓ context = build_case_generation_context(rid, doc_id?, ui_image_ids?)
   ↓ model 支持 vision → gateway.chat_with_images(prompt, images, model_config)
     否则 → ocr_extract 逐图 → 文字拼进 prompt → gateway.chat(prompt, model_config)
   ↓ 解析 JSON → 批量插 ai_case_drafts(status=pending)
     每个 step 带 needs_ui_detail 标记，AI 不确定时打 true
[ai_runs(status=succeeded), output_payload={batch_id, draft_count}]
   ↓
[CaseDraftReviewDialog] 按 model/批次分组渲染草稿；行内编辑；勾选
   - needs_ui_detail=true 的 step 黄色高亮 + "需补 UI 细节" 徽标
   ↓ POST /api/ai/case-drafts/commit  （target_module_id 默认 = requirement.module_id）
[test_cases]  新增 N 行：source="ai_m7"、draft_id=回填、requirement_id=回填、module_id=同需求
[ai_case_drafts] 对应行 status="accepted"，committed_case_id 回填
```

## Scope（Out of M7）

- **自动化 step 生成**（`http_request` / `web_*` / `app_*` 的结构化 step） → M8，对应 `docs/ai_features_requirements.md` 功能 6/7
- **AI 用例审查 / 重复检测 / 覆盖率分析** → M8，对应功能 5
- **SSE 流式输出**：M7 仍沿用 M6 的"前端轮询 ai_runs.status"，不引入 SSE 通道
- **跨项目"项目上下文"知识库**（pgvector 向量检索 / 历史用例聚类） → 更远期
- **草稿清理 cron**：草稿膨胀后的清理任务留 TODO，M7 暂不实装

---

## 已锁定的设计决策

| # | 主题 | 决策 |
|---|---|---|
| 1 | 输入源 | `requirement_id`（必须，1-N 个）+ `analysis_document_id`（可选，单个；给则当首要上下文）+ `ui_image_attachment_ids[]`（可选，PM 提供的页面截图；走 Attachment 表 kind=file） |
| 2 | 产物类型 | **functional 用例草稿**；字段：`title / preconditions / steps_text / expected / priority / tags / step_template`。`steps_text` 严格**业务流粒度**（不到 UI 控件级别），由 prompt 强约束 |
| 3 | 草稿表 | 新建 `ai_case_drafts`，与 `test_cases` 完全解耦；状态枚举 `pending` / `accepted` / `rejected` |
| 4 | Step 预留 + UI 不确定标记 | 草稿 JSON 列 `step_template = [{step_type, hint, needs_ui_detail: bool}, ...]`。`needs_ui_detail=true` = AI 没在 UI 截图/分析文档里找到对应控件，提示 PM 补全。`step_type` 留 M8 推自动化 step 用 |
| 5 | 模型配置 | **完全复用 M6 的 `AiModelConfig`** + `config_store(config_group="ai_models")`，**不新建任何模型表** |
| 6 | 多模型 | 沿用 M6 语义："一个 model → 一份 draft 批次"；同需求 + 同次提交可勾选多模型并行跑，每个 model 独立产出一批 drafts，不做后端 vote-merge |
| 7 | 数量控制 | 触发表单：`count_per_requirement` (枚举 5/10/15)、`scenario_mix` (`positive_only` / `positive_and_negative` / `full_coverage`) |
| 8 | 入库语义 | "批量入库" = 选中草稿一次性 insert `test_cases`：`source="ai_m7"`、`draft_id=<draft.id>`、`requirement_id=<draft.requirement_id>`、`module_id=<draft.requirement.module_id>`（默认；commit 表单可 override 整批 `target_module_id`）；草稿 `status` 改 `accepted`，`committed_case_id` 回填 |
| 9 | 幂等保护 | 同 `requirement_id` 在 30s 内重复提交 → 后端 409，提示"已有 running 批次 batch_id=xxx" |
| 10 | 上下文构建 | **复用 `requirement_context_builder.build_requirement_context`**；新增 wrapper 注入 ① analysis document markdown（截 8000 字）② UI 截图（model 支持 vision → base64；否则 OCR 抽文字）③ 上次同需求已 accept 用例摘要（避免重复生成） |
| 11 | JSON 解析 | OpenAI/Anthropic 启用 `response_format={"type":"json_object"}`；ollama / custom 走"提示中要求 ```json``` 围栏 → 后端正则提取 → `json.loads`"；三道兜底失败则把原文存 `ai_runs.output_payload.raw` + run 仍标 succeeded，前端提示"解析失败，可查看 raw" |
| 12 | 步骤粒度策略 | prompt 明确要求：① 写业务流动作，不写 UI 控件细节；② 有 UI 截图时按截图具体化入口/按钮文案；③ 无截图或截图未覆盖的步骤 → 用占位符 `[需PM补充：xxx]` + `needs_ui_detail=true` |
| 13 | 需求 ↔ 用例直连 | `test_cases` 新增 `requirement_id INT NULL FK requirements.id ON DELETE SET NULL`；commit 时回填。未来"需求变更 → 受影响用例"一句 SQL 可查；手写用例可留空不影响 |
| 14 | UI 截图来源 | 复用 M5 `attachments` 表（`kind=file`）。Launcher 既可勾选当前需求已有的图片附件，也可即时上传新图（后端走 `/api/requirements/{rid}/attachments` 接口落盘后返回 id，再带进生成请求） |
| 15 | functional 用例结构化保留业务步骤（**为 M8 留接口**） | `test_cases` 新增 `business_steps JSON` 列：`[{order, action_text, step_type_hint, needs_ui_detail}, ...]`。commit 时由 `draft.steps_text` + `draft.step_template` 行匹配拆解写入。M7 编辑页仍按 `description` 字符串编辑（保持现状不破坏），M7.1 可加结构化编辑。M8「functional → UI 自动化」直接读这列，**避免反查 draft 拿到 PM 编辑前的过时步骤** |

---

## 1. 数据层改造

### 1.1 Alembic 迁移 `m7_0001_ai_case_drafts.py`

**新表 `ai_case_drafts`：**

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `requirement_id` | int FK requirements.id ON DELETE CASCADE | |
| `analysis_document_id` | int FK requirement_analysis_documents.id nullable ON DELETE SET NULL | |
| `ai_run_id` | int FK ai_runs.id ON DELETE SET NULL | |
| `batch_id` | str(36) | uuid4；同一次提交的所有草稿共享 |
| `model_label` | str(100) | 生成时模型展示名（`openai / gpt-4o`） |
| `title` | str(255) | 用例标题 |
| `preconditions` | Text | 前置条件 |
| `steps_text` | Text | 多行 markdown 步骤（编号列表） |
| `expected` | Text | 预期结果 |
| `priority` | int default 2 | 0/1/2/3，对齐 test_cases.priority |
| `tags` | JSONType | `["smoke","regression"]` |
| `step_template` | JSONType nullable | `[{step_type, hint, needs_ui_detail: bool}, ...]`；M7 仅 AI 填 + 前端高亮 needs_ui_detail，M8 据 step_type 推自动化 step |
| `ui_image_refs` | JSONType nullable | 生成时引用到的 attachment id 列表，便于追溯"这条草稿基于哪几张截图" |
| `status` | str(20) default `pending` | `pending` / `accepted` / `rejected` |
| `committed_case_id` | int FK test_cases.id nullable | commit 后回填 |
| `created_by_id` | int FK users.id | |
| `created_at`, `updated_at` | datetime | |

索引：`(requirement_id, status, created_at desc)`、`batch_id`、`ai_run_id`、`committed_case_id`

**`test_cases` 表 schema 微调（同迁移内）：**

| 列 | 操作 | 类型 / 默认 |
|---|---|---|
| `source` | ADD COLUMN | `VARCHAR(20) DEFAULT 'manual' NOT NULL` |
| `draft_id` | ADD COLUMN | `INT NULL FK ai_case_drafts.id ON DELETE SET NULL` |
| `requirement_id` | ADD COLUMN | `INT NULL FK requirements.id ON DELETE SET NULL`，索引 `(requirement_id)` |
| `business_steps` | ADD COLUMN | `JSON NULL`，结构 `[{order, action_text, step_type_hint, needs_ui_detail}, ...]`，**M8 用** |

不动 `name / case_type / priority / tags / module_id / version_id` 等既有字段。手写用例的 `requirement_id` 留空，不影响现有数据；后续 PM 也可在用例详情页手工绑定需求（M7.1 可选增强）。

### 1.2 Pydantic schema `database/schemas/ai_case_draft.py`（新文件）

```python
class CaseGenerationTrigger(BaseModel):
    requirement_ids: list[int]
    analysis_document_id: int | None = None
    ui_image_attachment_ids: list[int] = []        # 走 attachments 表（kind=file），可空；M6 已支持 vision/OCR 双路径
    model_names: list[str]                         # M6 AiModelConfig.name
    count_per_requirement: Literal[5, 10, 15] = 5
    scenario_mix: Literal["positive_only", "positive_and_negative", "full_coverage"] = "positive_and_negative"
    user_prompt: str | None = None

class AiCaseDraftOut(BaseModel):
    id: int
    requirement_id: int
    analysis_document_id: int | None
    ai_run_id: int | None
    batch_id: str
    model_label: str
    title: str
    preconditions: str | None
    steps_text: str
    expected: str | None
    priority: int
    tags: list[str]
    step_template: list[dict] | None
    status: str
    committed_case_id: int | None
    created_at: datetime
    updated_at: datetime

class AiCaseDraftUpdate(BaseModel):
    title: str | None = None
    preconditions: str | None = None
    steps_text: str | None = None
    expected: str | None = None
    priority: int | None = None
    tags: list[str] | None = None

class CommitDraftsRequest(BaseModel):
    draft_ids: list[int]
    target_module_id: int | None = None            # 不填 = 用各 draft 关联需求的模块 / AI 子模块
```

### 1.3 模型类

- 新建 `database/models/ai_case_draft.py`：`AiCaseDraft`
- 修改 `database/models/test_case.py`：在 `TestCase` 上加 `source` / `draft_id` / `requirement_id` 三列 + 反向关系 `draft = relationship("AiCaseDraft", ...)` + `requirement = relationship("Requirement", ...)`
- `database/models/requirement.py`：加反向 `test_cases = relationship("TestCase", back_populates="requirement", lazy="select")`
- 在 `database/models/__init__.py` 导出 `AiCaseDraft`

---

## 2. 后端 API

### 2.1 用例生成入口 `server/api/ai_case_generation.py`（新文件）

```
POST   /api/ai/case-generation                  触发批次
       body: CaseGenerationTrigger（含 ui_image_attachment_ids[]）
       返回: { batches: [{batch_id, requirement_id, run_id, model_name}] }
       行为: 为每个 (requirement, model) 组合创建 1 个 ai_run（status=pending）+ 1 个 batch_id；任务 .delay() 异步处理。
             ui_image_attachment_ids 仅校验"附件属于这些 requirement 的 attachments 或属于同 project"，**不在请求里直接传图片二进制**——避免 body 膨胀

GET    /api/ai/case-drafts                      列草稿
       query: requirement_id? batch_id? status? page? page_size?
       返回: { items: AiCaseDraftOut[], total }

GET    /api/ai/case-drafts/{id}                 单条详情

PUT    /api/ai/case-drafts/{id}                 编辑草稿
       body: AiCaseDraftUpdate
       约束: status 必须 = pending 才能改

DELETE /api/ai/case-drafts/{id}                 拒绝草稿（status=rejected，不物理删）

POST   /api/ai/case-drafts/commit               批量入库
       body: CommitDraftsRequest
       返回: { created_case_ids: [int], skipped: [{draft_id, reason}] }
       事务: 逐条 insert test_cases → 回填 draft.committed_case_id → status=accepted

GET    /api/ai/case-generation/runs/{run_id}    查任务进度
       返回: ai_runs 对应行 + output_payload
```

权限：沿用 `server/api/deps.py` 的会话用户 + 项目成员校验，所有路由都要求 `requirement` 属于 user 可见 project。

幂等：`POST /api/ai/case-generation` 进入时查 `ai_runs WHERE requirement_id IN (...) AND task_type='case_generation' AND status IN ('pending','running') AND created_at > now() - 30s` →  非空则 409 并附 `running_batch_id`。

### 2.2 上下文构建器 `server/services/case_generation_context_builder.py`（新文件）

```python
def build_case_generation_context(
    session: Session,
    requirement_id: int,
    analysis_document_id: int | None = None,
    ui_image_attachment_ids: list[int] | None = None,
    existing_case_excerpt_limit: int = 10,
) -> CaseGenerationContext:
    """
    {
      "requirement": <build_requirement_context 的 requirement 段>,
      "module":      <同上>,
      "depends_on":  <同上>,
      "children":    <同上>,
      "attachments": <同上 documents 段，最长 4000 字/文件>,
      "analysis_markdown": str | None,                # current_markdown，截 8000 字
      "ui_images": [                                  # M7 新增：用户显式选作 UI 参考的截图
          {"attachment_id": int, "name": str, "abs_path": str, "mime": str},
          ...                                         # 上限 5 张；base64 由 gateway 按需现填，不在 builder 里读
      ],
      "existing_cases_excerpt": [
          {id, name, priority, tags},                 # 同需求关联模块下已有 test_cases 摘要，给 AI 做去重提示
      ],
    }
    """
```

实现要点：
- **直接调用** M6 的 `requirement_context_builder.build_requirement_context(session, rid)` 拿前 4 段
- 若 `analysis_document_id` 给定：查 `RequirementAnalysisDocument`、确认 `requirement_id` 匹配（不匹配抛 400）、读 `current_markdown` 截 8000 字
- `ui_images`：根据 `ui_image_attachment_ids` 查 `Attachment` 表（必须 `kind=file` 且 mime 在白名单 `image/png|image/jpeg|image/webp`，超出忽略）；上限 5 张（超出截断 + 警告）；只读元数据 + 绝对路径，不在 builder 里读 base64
- `existing_cases_excerpt`：`SELECT id, name, priority, tags FROM test_cases WHERE module_id = requirement.module_id LIMIT 10`，仅给 AI 做去重提示，不参与 commit

### 2.3 任务 handler `tasks/ai_tasks.py::_handle_case_generation`

注册到 `_HANDLERS = {..., "case_generation": _handle_case_generation}`。

流程：

```
1. 读 ai_runs 行 → input_payload = {requirement_id, analysis_document_id?, ui_image_attachment_ids[]?,
                                     model_name, batch_id, count, scenario_mix, user_prompt?}
2. ctx = build_case_generation_context(session, requirement_id, analysis_document_id, ui_image_attachment_ids)
3. 读 prompt 模板 ai_gateway/prompts/case_generation_v1.md
4. 替换 {{REQUIREMENT_TITLE}} / {{REQUIREMENT_DESCRIPTION}} / {{MODULE_INFO}} /
       {{DEPENDS_ON}} / {{CHILDREN}} / {{ATTACHMENT_EXCERPTS}} /
       {{ANALYSIS_MARKDOWN}} / {{EXISTING_CASES_EXCERPT}} /
       {{UI_IMAGE_HINTS}} /                       # 见下方分支
       {{COUNT}} / {{SCENARIO_MIX}} / {{USER_PROMPT}}
5. 取 AiModelConfig from config_store(group="ai_models", key=model_name)
6. UI 截图分支（沿用 M6 已实现的 vision/OCR 双路径）：
     若 ctx.ui_images 非空 且 model_config.supports_vision == True:
         {{UI_IMAGE_HINTS}} = "见随附图片，请基于截图中可见的入口/按钮/字段具体化步骤"
         resp = gateway.chat_with_images(prompt, images=ctx.ui_images, model_config, response_format=...)
     elif ctx.ui_images 非空 且 model 不支持 vision:
         ocr_texts = [gateway.ocr_extract(img.abs_path) for img in ctx.ui_images]
         {{UI_IMAGE_HINTS}} = "以下是页面 OCR 文字（按图片顺序）：\n" + "\n---\n".join(ocr_texts)
         resp = gateway.chat(prompt, model_config, response_format=...)
     else:
         {{UI_IMAGE_HINTS}} = "（PM 未提供 UI 截图。无法确认的具体入口/按钮请用占位符 [需PM补充：xxx] 并设 needs_ui_detail=true）"
         resp = gateway.chat(prompt, model_config, response_format=...)
7. 解析输出：
     a) 若 json mode → json.loads(raw) 取 cases 数组
     b) 否则正则 ```json\n(.*?)\n``` 提取 → json.loads
     c) 三道兜底失败 → ai_runs.output_payload = {"raw": raw, "parse_error": "..."}
                       仍标 status="succeeded"（让前端能看到原文），draft_count=0
8. 批量插 ai_case_drafts(status=pending, batch_id=..., ui_image_refs=[...attachment_id])
9. ai_runs.output_payload = {batch_id, draft_count: N}
10. ai_runs.status = "succeeded"
```

**多模型并行**：API 层为每个 `(requirement_id, model_name)` 创建独立 ai_run，Celery 各自消费，互不影响。

### 2.4 Prompt 模板 `ai_gateway/prompts/case_generation_v1.md`（新文件）

骨架：

```
你是一名资深测试工程师，请根据以下需求信息产出 functional 测试用例。

## 需求标题
{{REQUIREMENT_TITLE}}

## 需求描述
{{REQUIREMENT_DESCRIPTION}}

## 所属模块
{{MODULE_INFO}}

## 依赖需求
{{DEPENDS_ON}}

## 子需求
{{CHILDREN}}

## 附件摘要（PDF / DOCX / TXT 节选）
{{ATTACHMENT_EXCERPTS}}

## AI 已产出的分析文档（首选参考）
{{ANALYSIS_MARKDOWN}}

## UI 截图参考
{{UI_IMAGE_HINTS}}

## 该模块已有用例（避免重复产出）
{{EXISTING_CASES_EXCERPT}}

## 用户补充
{{USER_PROMPT}}

---

## 产出要求

- 数量：**恰好 {{COUNT}} 条**
- 场景覆盖：{{SCENARIO_MIX}}
   - positive_only：全部正向流程
   - positive_and_negative：~70% 正向 + ~30% 边界/异常
   - full_coverage：正向 / 异常 / 边界 / 安全 / 兼容 全覆盖

### 步骤写作粒度（重要）

- **写业务流动作**，不写 UI 控件细节。例如："3. 用户提交退款申请并选择原因『商品质量问题』" ✅；不要写"3. 点击屏幕右下角第二个蓝色按钮" ❌
- 一条用例 3–8 步为宜，覆盖完整业务路径
- 如果上面提供了 UI 截图 / OCR 文字，且其中能看出**具体入口名 / 按钮文案 / 字段名**，把它写进步骤里（提高 PM 信任感）
- 如果某一步**没有任何可参考信息**（截图缺失 / 分析文档没提 / 需求描述模糊）：
   - 步骤文本里用占位符 `[需PM补充：具体如何 xxx]`
   - 该步对应的 `step_template[i].needs_ui_detail` 设为 `true`
   - **不要瞎编 UI 元素**

### 字段约定（严格 JSON）

   ```json
   {
     "cases": [
       {
         "title": "string，≤80 字，描述用例点",
         "preconditions": "string，前置条件，无则空串",
         "steps_text": "string，markdown 编号列表，每行一步",
         "expected": "string，预期结果，可多行",
         "priority": 0|1|2|3,
         "tags": ["smoke"|"regression"|...],
         "step_template": [
           {
             "step_type": "http_request|web_click|web_input|app_tap|assert|business_action|...",
             "hint": "用自然语言描述这一步要做什么（与 steps_text 第 i 行对应）",
             "needs_ui_detail": false
           }
         ]
       }
     ]
   }
   ```

- `step_template` 数组长度 = `steps_text` 编号行数（一一对应）
- 只输出 JSON，不要其他文字；如必须解释请放进 `__note` 顶层字段，后端会忽略
```

### 2.5 Service 层 `server/services/ai_case_draft_service.py`（新文件）

公共函数：

- `list_drafts(session, requirement_id?, batch_id?, status?, page, page_size) -> tuple[list[AiCaseDraft], int]`
- `get_draft(session, draft_id) -> AiCaseDraft`
- `update_draft(session, draft_id, payload: AiCaseDraftUpdate) -> AiCaseDraft`（status 非 pending 抛 409）
- `reject_draft(session, draft_id) -> AiCaseDraft`
- `batch_commit(session, draft_ids: list[int], target_module_id: int | None) -> CommitResult`
  - 模块归属：若 `target_module_id` 给定 → 整批用该值；否则**每条 draft 取 `draft.requirement.module_id`**（每条都查一次）；若需求未绑模块 → 该条跳过并加入 `skipped[]`
  - **`business_steps` 构造**：用 `re.findall(r'^\s*\d+\.\s*(.+)$', draft.steps_text, re.M)` 解析编号行；按下标与 `draft.step_template[i]` 对齐合并 → `[{order: i+1, action_text: <编号行文本>, step_type_hint: step_template[i].step_type, needs_ui_detail: step_template[i].needs_ui_detail}, ...]`；行数与 step_template 不一致时以 steps_text 为准、缺位补默认 `{step_type_hint: "business_action", needs_ui_detail: true}`
  - 事务：select drafts → 逐条 build `TestCase(name=title, description=preconditions+steps+expected concat, case_type="functional", module_id=<上面规则>, requirement_id=draft.requirement_id, priority=..., tags=..., source="ai_m7", draft_id=draft.id, business_steps=<上面构造结果>)` → flush 拿 case_id → draft.committed_case_id = case_id, status = "accepted"
  - 失败回滚整批

### 2.6 路由注册

- `server/api/__init__.py` 导出 `ai_case_generation_router`
- `server/main.py` 的 router 循环里加进去（自动挂 `/api` 前缀）

---

## 3. 前端改造

### 3.1 类型 `frontend/src/types/domain.ts`

```typescript
export interface AiCaseDraft {
  id: number;
  requirement_id: number;
  analysis_document_id: number | null;
  ai_run_id: number | null;
  batch_id: string;
  model_label: string;
  title: string;
  preconditions: string;
  steps_text: string;
  expected: string;
  priority: number;
  tags: string[];
  step_template: { step_type: string; hint: string; needs_ui_detail: boolean }[] | null;
  ui_image_refs: number[] | null;                  // 生成时引用到的 attachment id
  status: "pending" | "accepted" | "rejected";
  committed_case_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface CaseGenerationBatch {
  batch_id: string;
  requirement_id: number;
  run_id: number;
  model_name: string;
}
```

### 3.2 API 层 `frontend/src/lib/api.ts`

```typescript
export const aiCaseGenerationApi = {
  trigger: (body: {
    requirement_ids: number[];
    analysis_document_id?: number;
    ui_image_attachment_ids?: number[];
    model_names: string[];
    count_per_requirement: 5 | 10 | 15;
    scenario_mix: "positive_only" | "positive_and_negative" | "full_coverage";
    user_prompt?: string;
  }) => request<{ batches: CaseGenerationBatch[] }>('/api/ai/case-generation', { method: 'POST', body }),

  listDrafts: (params: { requirement_id?: number; batch_id?: string; status?: string; page?: number; page_size?: number }) =>
    request<{ items: AiCaseDraft[]; total: number }>('/api/ai/case-drafts', { params }),

  updateDraft: (id: number, body: Partial<AiCaseDraft>) =>
    request<AiCaseDraft>(`/api/ai/case-drafts/${id}`, { method: 'PUT', body }),

  rejectDraft: (id: number) =>
    request<void>(`/api/ai/case-drafts/${id}`, { method: 'DELETE' }),

  commit: (body: { draft_ids: number[]; target_module_id?: number }) =>
    request<{ created_case_ids: number[]; skipped: { draft_id: number; reason: string }[] }>(
      '/api/ai/case-drafts/commit', { method: 'POST', body }
    ),

  getRun: (id: number) => request<AiRun>(`/api/ai/case-generation/runs/${id}`),
};
```

### 3.3 三个入口（用户全选）

| 入口 | 文件 | 改动 |
|---|---|---|
| **A · 分析文档 Viewer 按钮** | `pages/requirements/viewers/AnalysisDocumentViewerDialog.tsx` | 工具栏新增 `Wand2` 图标按钮"AI 生成用例" → 打开 `CaseGenerationLauncherDialog`，默认带入 `requirement_id` + `analysis_document_id` |
| **B · 需求行操作列按钮** | `pages/RequirementsPage.tsx`（或重构后的 `requirements` 子页面） | 行尾新增 `ListPlus` 图标按钮 → 打开 `CaseGenerationLauncherDialog`，仅带 `requirement_id` |
| **C · 需求列表批量勾选** | 同上 | 表头已有 selection bar；新增"批量 AI 生成用例"按钮 → 打开 Launcher，传 `requirement_ids[]` |

### 3.4 新组件

| 组件 | 路径 | 职责 |
|---|---|---|
| `CaseGenerationLauncherDialog.tsx` | `pages/requirements/dialogs/` | 表单：① model 多选（从 `/api/ai-models?enabled=true` 拉）② count_per_requirement 单选 (5/10/15) ③ scenario_mix 单选 ④ **UI 截图区**：列出该需求 `attachments` 里 mime=image/* 的图，可勾选；下方"上传新截图"按钮 → multipart 上传到 `/api/requirements/{rid}/attachments` 后自动勾选 ⑤ `user_prompt` textarea。提交 → toast → 自动打开 `CaseDraftReviewDialog`（loading state） |
| `CaseDraftReviewDialog.tsx` | `pages/requirements/dialogs/` | 标签页布局：按 `(requirement_id, model_label)` 分组（多模型/多需求时多个 tab）；每 tab 内一条草稿一行；行内可编辑 + 多选；**`steps_text` 渲染时按行匹配 `step_template[i].needs_ui_detail`，true 的行加黄色左边框 + "需补 UI 细节" 徽标**；顶部 alert 提示"共 N 个 step 待补 UI 细节"；右下角"批量入库"按钮（弹 `target_module_id` 选择器，默认 = 当前需求 module，未绑则强制让 PM 选） |
| `CaseDraftRow.tsx` | `pages/requirements/components/` | 单条草稿展示 / 编辑切换；编辑模式用 `MarkdownEditor` 编辑 `steps_text`，`Input` 编 title，`Textarea` 编 preconditions/expected；展示模式按行高亮 needs_ui_detail |
| 复用 | `pages/cases/CasesTab` | 入库后用例自动出现在标准用例列表（`source` 列加 badge "AI 生成"），无需新建页面 |

### 3.5 Toast & 进度轮询

`CaseDraftReviewDialog` 收到 batches 后：

```
for (const b of batches) {
  startPolling(b.run_id, intervalMs=3000, maxAttempts=60);
  // 单 run succeeded → refetch listDrafts(batch_id=b.batch_id) → 渲染对应 tab
  // 单 run failed   → tab 显示错误 message + 错误图标
}
```

所有 batches 完成后停止轮询。用户可以一个 tab 加载完就开始编辑，不需要等全部完成。

### 3.6 `CasesTab` 微调

`pages/cases/CasesTab.tsx` 表格"来源"列：

- `source === "ai_m7"` → 显示 `<Badge variant="secondary">AI 生成</Badge>` + 鼠标 hover 显示 `draft_id`
- `source === "manual"` 或缺省 → 不显示徽标

---

## 4. 任务拆分（commit 前缀 `feat(ai-m7):`）

| # | Task | 类型 | 关键文件 |
|---|---|---|---|
| 1 | Alembic 迁移：ai_case_drafts 新表 + test_cases.source/draft_id/requirement_id + Requirement 反向关系 | backend schema | `database/migrations/versions/m7_0001_ai_case_drafts.py`、`database/models/ai_case_draft.py`、`database/models/test_case.py`、`database/models/requirement.py`、`database/models/__init__.py` |
| 2 | Pydantic schema | backend | `database/schemas/ai_case_draft.py` |
| 3 | 上下文 builder | backend | `server/services/case_generation_context_builder.py` |
| 4 | Prompt 模板 v1 | backend | `ai_gateway/prompts/case_generation_v1.md` |
| 5 | Task handler `_handle_case_generation` + 注册 | backend | `tasks/ai_tasks.py` |
| 6 | Service 层（含 batch_commit 事务） | backend | `server/services/ai_case_draft_service.py` |
| 7 | API 路由 + 幂等校验 + deps | backend | `server/api/ai_case_generation.py`、`server/api/__init__.py`、`server/main.py` |
| 8 | 前端 api.ts + types | frontend | `frontend/src/lib/api.ts`、`types/domain.ts` |
| 9 | CaseGenerationLauncherDialog（含 UI 截图勾选 / 上传） | frontend | `pages/requirements/dialogs/` |
| 10 | CaseDraftReviewDialog + Row（含 needs_ui_detail 高亮） | frontend | `pages/requirements/dialogs/` + `pages/requirements/components/` |
| 11 | 三入口接线（Viewer / 行操作 / 批量条） | frontend | `viewers/AnalysisDocumentViewerDialog.tsx`、`RequirementsPage.tsx` |
| 12 | CasesTab source 徽标 + 关联需求列 | frontend | `pages/cases/CasesTab.tsx` |
| 13 | E2E smoke 验证（见 §8） | docs | 本文档 |

---

## 5. 关键复用点

- **`ai_gateway/gateway.py`** 现成 `chat` / `chat_with_images` / `ocr_extract`（M6 已实装的 vision + OCR 双路径） + provider 路由
- **M6 `AiModelConfig` + `config_store(group="ai_models")`** + `supports_vision: bool` 字段 —— 同一配置中心，PM 已熟悉
- **`server/services/requirement_context_builder.build_requirement_context`**（M6 已实现，**直接调用**作为底座）
- **`ai_runs` 表 + 现成 status 流转**（pending/running/succeeded/failed）+ `input_payload` / `output_payload` JSON 列，零 schema 改动
- **`database/models/attachment.py`**（M5）+ `/api/requirements/{rid}/attachments` 上传接口 —— UI 截图直接走这条管道，不另起新表
- **`frontend/src/components/editor/MarkdownEditor.tsx`** / `MarkdownView.tsx` 用于 `steps_text` 多行 markdown 编辑
- **`database/models/test_case.py` / `test_step.py`** 已存在 —— 只给 `test_cases` 加 `source / draft_id / requirement_id` 三列，不动主结构
- **`server/api/deps.py`** 项目成员校验 + DB session 注入直接套用
- **`pages/cases/CasesTab.tsx`** 既有列表组件，加来源徽标 + 关联需求列即可，无须新建用例页面

## 6. 必须新建

- 1 个 DB 表（`ai_case_drafts`）+ 模型类
- 1 个 service：用例生成上下文 builder
- 1 个 service：草稿 CRUD + batch_commit 事务
- 1 个 Celery handler（`_handle_case_generation`）
- 1 个 prompt 模板
- 1 个 API 路由文件
- 前端 3 个新组件（Launcher / ReviewDialog / DraftRow）+ 1 处入口接线 + 1 处 CasesTab 微调

## 7. 风险 & 待定

- **模型 JSON 输出不稳**：OpenAI/Anthropic 用 `response_format={"type":"json_object"}` 强约束；ollama / custom 用 prompt 围栏 + 正则 + `json.loads` 三道兜底；最坏存原文到 `ai_runs.output_payload.raw`，前端给"查看原文"按钮，不让 PM 一脸蒙
- **草稿膨胀**：M7 不自动清理；新增 TODO "M7.1 加 cron 每周清 `status=rejected` 且 `updated_at < now() - 90d`"
- **批量长时间运行**：N 需求 × M model = N×M 个 ai_run，Celery worker 并行消费；前端 `CaseDraftReviewDialog` 按 tab 渐进式渲染，单 run 完成就刷新对应 tab，**不阻塞用户编辑已完成 tab**
- **AI 瞎编 UI 步骤的风险**：通过三重约束规避 ——
  - prompt 强制业务流粒度（决策 #12）
  - UI 不确定时**必须**用 `[需PM补充：xxx]` 占位符 + `needs_ui_detail=true`
  - 前端 ReviewDialog 高亮所有 needs_ui_detail 步骤并在顶部 alert 显示"共 N 个 step 待补 UI 细节"，强提醒 PM 补全后再 commit
- **PM 未上传 UI 截图**：仍可生成，但 prompt `{{UI_IMAGE_HINTS}}` 段会明确告诉 AI "无截图、未知入口必须打占位符"；生成的 needs_ui_detail 比例必然升高，前端高亮自然引导 PM 补
- **token / vision 上限**：context builder 复用 M6 的文字截断策略（标题 100 字、描述 500 字、附件 4000 字/文件、analysis_markdown 8000 字、existing_cases 限 10 条）；UI 截图**上限 5 张**，超出截断 + 警告
- **同一需求短时间重复触发**：30s 内同 `requirement_id` 已有 running run → 409 + 提示已有 batch_id
- **commit 时模块归属边界情况**：
  - 表单未指定 `target_module_id` 且需求未绑模块 → 该条 draft 加入 `skipped[]` 返回，不阻塞其他条
  - 指定的 `target_module_id` 不属于当前项目 → 400 拒绝整批
- **需求-用例 FK 与既有用例**：M7 之前的手写用例 `requirement_id` 全为 `NULL`，不影响 SQL；后续可在用例详情页加"绑定需求"按钮（M7.1 增强）

---

## 8. Verification

### 后端 smoke

```bash
# 0. 准备：已运行 M6 的 ai-models 配置；已 alembic upgrade head
alembic upgrade head

# 1. 准备一个有 analysis document 的需求（用 M6 链路先跑一次分析）
#    假设最终 requirement_id=42、analysis_document_id=7

# 1.5 给该需求挂 2 张 UI 截图（沿用 M5 attachments 接口）
curl -X POST http://127.0.0.1:54351/api/requirements/42/attachments \
  -F 'file=@order_list.png' -F 'kind=file'
curl -X POST http://127.0.0.1:54351/api/requirements/42/attachments \
  -F 'file=@refund_dialog.png' -F 'kind=file'
# 假设返回 attachment_id 分别是 101 / 102

# 2. 触发用例生成（vision 模型 / 5 条 / 正向+异常 / 带 UI 截图）
curl -X POST http://127.0.0.1:54351/api/ai/case-generation \
  -H 'Content-Type: application/json' \
  -d '{
    "requirement_ids":[42],
    "analysis_document_id":7,
    "ui_image_attachment_ids":[101,102],
    "model_names":["gpt-4o"],
    "count_per_requirement":5,
    "scenario_mix":"positive_and_negative",
    "user_prompt":"重点覆盖支付失败回滚场景"
  }'
# 期望: 返回 1 个 batch + 1 个 run_id；生成的草稿步骤中能引用截图里看到的入口名（如"我的订单 → 退款"）

# 3. 轮询 run
curl http://127.0.0.1:54351/api/ai/case-generation/runs/<run_id>
# 期望: status: pending → running → succeeded，output_payload.draft_count == 5

# 4. 列草稿
curl 'http://127.0.0.1:54351/api/ai/case-drafts?requirement_id=42&status=pending' | jq
# 期望: items.length == 5；每条有 title / steps_text / expected / priority / tags / step_template
#       step_template[i].needs_ui_detail 在 AI 不确定的步骤上为 true
#       ui_image_refs == [101, 102]

# 5. 编辑第 3 条
curl -X PUT http://127.0.0.1:54351/api/ai/case-drafts/3 \
  -H 'Content-Type: application/json' \
  -d '{"title":"修订后的用例标题","steps_text":"1. 用户登录\n2. 进入订单页\n3. 触发退款"}'

# 6. 拒绝第 5 条
curl -X DELETE http://127.0.0.1:54351/api/ai/case-drafts/5
# 验证: 该条 status=rejected，list pending 看不到

# 7. 批量入库（1,2,3,4 共 4 条；不指定 target_module_id，默认走 draft.requirement.module_id）
curl -X POST http://127.0.0.1:54351/api/ai/case-drafts/commit \
  -H 'Content-Type: application/json' \
  -d '{"draft_ids":[1,2,3,4]}'
# 期望: created_case_ids 长度 4；test_cases 表里 4 条新行：
#       source="ai_m7"、draft_id 回填、requirement_id=42、module_id=requirements[42].module_id

# 7b. 也可以显式指定模块（override 整批）
curl -X POST http://127.0.0.1:54351/api/ai/case-drafts/commit \
  -d '{"draft_ids":[6,7], "target_module_id":99}'
# 期望: 这 2 条 module_id=99（不管 draft.requirement.module_id 是什么）

# 8. 验证幂等
curl -X POST http://127.0.0.1:54351/api/ai/case-generation \
  -d '{"requirement_ids":[42],"model_names":["gpt-4o"],"count_per_requirement":5,"scenario_mix":"positive_and_negative"}'
# 紧接 step 2 30s 内 → 期望 409 + running_batch_id

# 9. 多模型并行
curl -X POST http://127.0.0.1:54351/api/ai/case-generation \
  -d '{"requirement_ids":[43,44],"model_names":["gpt-4o","claude-3-7"],"count_per_requirement":5,"scenario_mix":"full_coverage"}'
# 期望: 返回 4 个 batches (2 req × 2 model)，4 个 run_id 各自异步消费
```

### 前端浏览器手测

1. `/projects/1/requirements` → 选有分析文档的需求 → 点行内 `ListPlus` 按钮 → Launcher 默认值合理（model 默认值 = M6 is_default 的 model）
2. Launcher 里 UI 截图区列出该需求所有 image 类型 attachments → 勾选 2 张 → 也可点"上传新截图"再加一张 → 提交
3. toast"已提交，正在生成" → 自动打开 ReviewDialog（loading 状态）→ ~10s 后渲染 5 条草稿
4. ReviewDialog 顶部 alert 显示"共 N 个 step 待补 UI 细节"，对应步骤行黄色高亮
5. 编辑第 3 条 title / steps_text（把占位符替换为真实步骤） / expected → 保存
6. 取消勾选第 5 条 → 点"批量入库"→ 模块下拉默认值 = 该需求所属模块 → 提交 → toast"已入库 4 条"
7. 切到 `/projects/1/cases` → 看到 4 条新用例：来源列显示"AI 生成"徽标 + 关联需求列显示"#42 xxx"
8. **入口 A**：进任意需求 → 打开分析文档 Viewer → 工具栏 `Wand2` 按钮 → 同一 Launcher，默认带 analysis_document_id；提交后 prompt 中包含分析文档全文
9. **入口 C**：需求列表勾 3 个需求 → "批量 AI 生成用例" → Launcher 显示"3 个需求 × 1 模型 = 3 批"→ 提交 → ReviewDialog 显示 3 个 tab，每个 tab 独立 loading / succeeded
10. **不带截图场景**：选一个完全没有 attachments 的需求 → 触发生成 → 期望 needs_ui_detail=true 的步骤明显增多，ReviewDialog 顶部 alert 数字也大

### 联动验证

- AI 模型 `enabled=false` → Launcher 列表里该 model 消失（沿用 M6 行为）
- 单个 run 失败（比如模型 quota 用完）→ 该 batch tab 显示错误信息 + 错误图标，**其他 batch 不受影响**
- 草稿 `status=accepted` 后再次 list pending → 看不到该条（避免重复入库）
- commit 后 `test_cases.draft_id` 回填，且 `/api/cases/<id>` 详情能反查到 draft 和 requirement
- 需求-用例直连：`SELECT * FROM test_cases WHERE requirement_id = 42` → 能查到刚入库的 4 条
- `target_module_id` 不属于当前项目 → 400 拒绝整批
- vision 测试：用 `supports_vision=false` 的 model（如 deepseek-chat）+ UI 截图 → 后端日志应显示走 OCR 分支；prompt 里包含 OCR 文字而非 base64
- JSON 解析失败：故意配一个会返回纯文本的 ollama 模型 → run.status=succeeded，draft_count=0，前端 ReviewDialog 显示"解析失败，[查看原文]"按钮 → 弹窗显示 `ai_runs.output_payload.raw`

### 每步通过

```bash
cd frontend && npm run typecheck && npm run lint
python -m compileall server tasks database ai_gateway runners
alembic upgrade head
```

### business_steps 联动验证

- commit 后查 `SELECT business_steps FROM test_cases WHERE draft_id=<x>` → JSON 数组长度等于 steps_text 编号行数
- 数组每项含 `order / action_text / step_type_hint / needs_ui_detail` 四字段
- `business_steps[i].needs_ui_detail == draft.step_template[i].needs_ui_detail`
- PM 在 ReviewDialog 编辑过 steps_text 之后再 commit → business_steps 反映编辑后的版本（**不是** draft 原始版本）

---

## 9. M8 衔接预留：功能用例 → UI 自动化用例

> 本节**仅描述 M8 蓝图**，不是 M7 工作量。目的是把 M7 现在留好的钩子（`business_steps` / `step_template` / `ui_image_refs` / `attachments` / `AiModelConfig` / `requirement_id` FK）映射到 M8 的接口轮廓上，让 M7 落地时不会被未来意外要求倒逼返工。

### 9.1 M8 目标与产物结构

- **输入**：M7 落地后的 functional `test_cases` 行集合（包含 `business_steps` 列）
- **产物**：**新建独立的 `test_cases` 行**（case_type ∈ `web / api / android / ios / mixed`）+ 关联的结构化 `TestStep` 行
- **追溯**：自动化用例 `test_cases.source_case_id` FK → 原 functional `test_cases.id`（决策已锁定）
- **不破坏**：原 functional 用例保留不动；同一 functional 用例可对应多次重生的自动化版本（最新一份 `is_active=true`，旧版保留可查）

迁移 `m8_0001_auto_case_generation.py` 草图：

| `test_cases` 加列 | 类型 | 说明 |
|---|---|---|
| `source_case_id` | `INT NULL FK test_cases.id ON DELETE SET NULL` | 自动化用例 → 来源 functional 用例 |
| `is_active` | `BOOLEAN DEFAULT true NOT NULL` | 同 source_case_id 下保留多版本时区分；执行列表只显示 active |

### 9.2 定位器 / 接口信息来源（**4 路并存**，按需混搭）

| 来源 | 适用场景 | M8 新增基础设施 |
|---|---|---|
| **page_objects 表** | web / app UI 自动化 | 新表 `page_objects (id, project_id, name, url_pattern, dom_snapshot TEXT, selectors JSON, screenshot_attachment_id)`；PM 维护，生成时按 page_object_id 关联 |
| **M7 attachments UI 截图** | 兜底；page_object 没建好的页面 | 直接复用 `ai_case_drafts.ui_image_refs` 关联的 attachments + vision 模型猜定位器；准确率比 page_object 低，标记 `needs_review=true` |
| **录制脚本上传** | 高保真自动化（Playwright codegen / Selenium recorder） | 新表 `recorded_scripts (id, project_id, name, format, content TEXT)`；AI 把录制脚本作为定位器底座 + functional 步骤作为业务包装，做参数化与断言注入 |
| **OpenAPI / Swagger** | API 用例（`case_type=api`） | 项目级 `api_specs (id, project_id, spec_format, content JSON)`；AI 据此推 `http_request` step 的 method/path/headers/body schema |

### 9.3 M8 接口轮廓（设计草图）

```
POST /api/ai/auto-case-generation                 触发
     body: {
       functional_case_ids: int[],
       target_case_type: "web"|"api"|"android"|"ios"|"mixed",
       sources: {
         page_object_ids?: int[],                # 9.2 #1
         use_m7_ui_images?: bool,                # 9.2 #2
         recorded_script_ids?: int[],            # 9.2 #3
         api_spec_ids?: int[],                   # 9.2 #4
       },
       model_names: string[],
       user_prompt?: string,
     }
     返回: { batches: [{batch_id, source_case_id, run_id, model_name}] }

GET    /api/ai/auto-case-drafts                  列草稿（复用 ai_case_drafts？看 9.4）
POST   /api/ai/auto-case-drafts/commit           批量入库 + 创建 TestStep 行
```

### 9.4 草稿表策略：**复用 vs 新建**（M8 时再定，但 M7 设计要兼容）

两种走法都能跑：

- **方案 a · 复用 `ai_case_drafts`**：加一列 `kind ENUM('functional','auto')`，functional 字段保持现状，auto 时 `steps_text` 字段语义变为"step 行预览（JSON 字符串）"，`step_template` 直接当作 step 行 list 用
- **方案 b · 新建 `ai_auto_case_drafts`**：和 functional 草稿语义差异大（有结构化 step rows），独立建表更清晰

为了不绑死 M7 的 `ai_case_drafts` schema：

- `ai_case_drafts.step_template` 列**保留为 generic JSON**，不加 NOT NULL constraint，方便 M8 复用时灌入更复杂结构
- `model_label / batch_id / ai_run_id / status` 这些通用字段在两个表里语义一致，**复用 SQLAlchemy mixin** 提取（M8 时再做）

### 9.5 M8 输入构造（用 M7 留好的列）

```python
# M8 service 入口（伪代码）
def build_auto_case_context(session, functional_case_id, target_type, sources):
    case = session.get(TestCase, functional_case_id)
    return {
        "functional_case": {
            "name": case.name,
            "description": case.description,
            "business_steps": case.business_steps,      # ← M7 留的钩子，核心
            "requirement_id": case.requirement_id,      # ← M7 留的钩子
            "tags": case.tags,
            "priority": case.priority,
        },
        "requirement": build_requirement_context(...),  # 复用 M6
        "ui_images": [resolve_attachment(a) for a in (
            case.draft.ui_image_refs if case.draft else []  # M7 已落
        )],
        "page_objects":     [...by sources.page_object_ids...],   # M8 新增
        "recorded_scripts": [...by sources.recorded_script_ids...], # M8 新增
        "api_specs":        [...by sources.api_spec_ids...],        # M8 新增
    }
```

### 9.6 关键："needs_ui_detail" 的语义传递

- M7 functional 用例的 `business_steps[i].needs_ui_detail == true` 表示"PM 也没补，业务粒度模糊"
- M8 生成自动化用例时遇到 `needs_ui_detail==true` 的 step：
   - 若有 page_object 命中 → 仍可生成可执行 step，但在 step.config 标 `inferred_from_page_object: true`
   - 都没有 → 该 step 跳过 / 留 placeholder step（`step_type=todo, hint=...`），返回时聚合提示 PM 这条 case 转换率
- 这样 M7 阶段的 PM 行为（认真补还是糊弄了事）**会自然影响 M8 的转换质量**，不会出现"M7 草草通过 → M8 全自动 → 跑出来全错"的暗坑

### 9.7 M7 已经留好的钩子清单（M8 直接拿来用）

| M7 资产 | M8 用法 |
|---|---|
| `test_cases.business_steps` | M8 输入的核心（去掉对 `draft_id` 反查的依赖） |
| `test_cases.requirement_id` | M8 把需求上下文一起喂给 AI |
| `ai_case_drafts.ui_image_refs` | M8 在 PM 没建 page_object 时的兜底定位器来源 |
| `ai_case_drafts.step_template[i].step_type` | M8 优先生成对应类型的可执行 step |
| `AiModelConfig` + `chat_with_images` + `ocr_extract` | 模型路由与 vision/OCR 通道完全复用 |
| `ai_runs` 表 | 任务编排与状态流转复用 |
| `build_requirement_context` | 需求上下文构建器复用 |

### 9.8 M7 期间**不要做**的 M8 工作

- 不在 M7 里新建 `page_objects` / `recorded_scripts` / `api_specs` 表（保持 M7 scope）
- 不在 M7 里给 `test_cases` 加 `source_case_id` / `is_active` 列（同上）
- 不在 M7 用例编辑页加结构化"业务步骤"编辑器（M7.1 增强项）
- 不在 M7 里写 `_handle_auto_case_generation` handler（M8 实装）
