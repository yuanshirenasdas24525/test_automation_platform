# AI 功能用例 → UI 自动化用例 · M8 实施文档

> commit 前缀：`feat(ai-m8):`
> 前置里程碑：M7（AI 生成功能用例 + `ai_case_drafts` + `test_cases.business_steps`）
> 宏观蓝图来源：`docs/ai_case_generation_m7_plan.md` §9「M8 衔接预留」、`docs/ai_features_requirements.md` 功能 6/7
> 状态：**设计稿，selector 来源方案待 owner 拍板（见 §3）**
> 专题目录索引：[`README.md`](./README.md)

## Context

M7 把"需求 → AI 生成 functional 用例 → PM review → 入库"的闭环跑通了。落地后每条 functional `test_cases` 行带了一个关键钩子列 `business_steps`：

```json
[{"order": 1, "action_text": "用户访问注册页", "step_type_hint": "navigate", "needs_ui_detail": false}, ...]
```

但 functional 用例**不可执行**——它是"业务流粒度"的人读步骤，没有 selector、没有具体值、没有断言定位。要让"AI 写的用例"真正"AI 跑起来"，必须把 `business_steps` 反推成执行引擎认的结构化 `TestStep`。这就是 M8。

本文档要解决一件事：**给两套"标准格式"下精确定义，并打通它们之间的反推桥，使生成的 UI 用例能在现有 v2 执行链路上一键执行。**

当前代码现状（已确认）：

- 执行引擎 `runners/steps/web_actions.py` 已实装 8 个 web step runner，认 `TestStep.config` 契约——**执行侧不用动**。
- `tasks/ai_tasks.py` 的 `_HANDLERS` 没有任何"自动化用例生成" handler。
- `server/api/` 下没有 auto-case 相关路由。
- `database/models/test_case.py` 已有 `business_steps` / `source` / `draft_id` / `requirement_id` 列（M7 落），但**没有** `source_case_id` / `is_active`。
- 没有 `page_objects` 表，没有任何 selector 知识库。

---

## 两套标准格式（本文档的核心交付）

### 格式 A · 功能用例标准格式（M8 的输入，M7 已产出）

functional `test_cases` 行 + `business_steps` JSON 列。M8 **只读不写**这套，且**只读 `business_steps`，不反查 `draft`**（避免拿到 PM 编辑前的过时步骤——M7 §9.6 已锁定此点）。

| 字段 | 来源 | M8 用法 |
|---|---|---|
| `name` / `description` | test_cases | 喂给 AI 做整体语义 |
| `business_steps[]` | test_cases（M7 commit 时合成） | **反推主输入**，逐项映射成 TestStep |
| `business_steps[i].order` | 同上 | 决定 `TestStep.step_order` |
| `business_steps[i].action_text` | 同上 | AI 理解这一步要做什么 |
| `business_steps[i].step_type_hint` | 同上（`navigate/input/select/submit/verify/wait/cleanup/other`） | AI 选 web step type 的强先验 |
| `business_steps[i].needs_ui_detail` | 同上 | true = 业务粒度模糊，转换质量预警信号 |
| `requirement_id` | test_cases（M7 FK） | 拉需求上下文一起喂 AI |
| `priority` / `tags` | test_cases | 透传到自动化用例 |

`step_type_hint → web step type` 的默认映射（AI 可覆盖，但默认走这张表）：

| hint | 默认 web step_type | 备注 |
|---|---|---|
| `navigate` | `web_goto` | 需要 url |
| `input` | `web_input` | 需要 by+locator+value |
| `select` | `web_select` | 需要 by+locator+value\|label\|index |
| `submit` | `web_click` | 提交按钮 |
| `verify` | `web_assert_text` | 需要 by+locator+equals\|contains\|regex |
| `wait` | `web_wait` | by+locator+state 或 seconds/ms |
| `cleanup` | `web_evaluate` 或 `web_click` | 视情况 |
| `other` | 由 AI 判定 | 落不到则标 todo |

### 格式 B · UI 自动化用例标准格式（M8 的产物，必须可一键执行）

落成 `test_cases(case_type='web', source='ai_m8_auto')` + 一串 `TestStep` 行。**这套格式不是新发明的，是执行引擎 `web_actions.py` 现成认的契约**，M8 必须严格对齐，否则生成出来跑不了。

**TestStep 行结构（来自 `database/models/test_step.py`）：**

```
step_order   int       从 business_steps[i].order 来
step_name    str       人读名，取 action_text 摘要
step_type    str       见下方枚举
skip         bool      默认 false
config       JSON      按 step_type 不同，见下表 —— 核心载荷
extract      JSON      [{name, from, jsonpath}]，UI 一般用不上，留空
assertion    JSON      [{type, target, expected}]，可选
wait_before  float     默认 0
timeout      int       默认 30
retry        int       默认 0
on_failure   str       stop | continue | retry，默认 stop
```

**web step_type 枚举（8 种，来自 `runners/steps/web_actions.py`）+ config 契约：**

| step_type | config 字段 | 必填 |
|---|---|---|
| `web_goto` | `{url, timeout?}` | url |
| `web_click` | `{by, locator, timeout?}` | by, locator |
| `web_input` | `{by, locator, value, clear_first?, timeout?}` | by, locator, value |
| `web_select` | `{by, locator, value?, label?, index?, timeout?}` | by, locator + 三选一 |
| `web_wait` | `{by, locator, state?}` 或 `{seconds}` / `{ms}` | 二选一组合 |
| `web_assert_text` | `{by, locator, equals?, contains?, regex?, timeout?}` | by, locator + 三选一 |
| `web_screenshot` | `{name?, path?}` | 无 |
| `web_evaluate` | `{script, args?, save_as?}` | script |

**`by` 取值（来自 `runners/web/adapters.py` `BY_TYPES`，仅这 7 种）：**

```
css | xpath | id | name | class | text | link
```

> 硬约束：AI 生成的每个 `web_click` / `web_input` / `web_select` / `web_assert_text` / `web_wait(元素态)` step，其 `config.by` **必须**∈ 上述 7 种，`config.locator` 非空。否则 `_require_by_locator` 会在执行期 `raise ValueError`，dispatcher 兜底成 ERROR。M8 在入库前要做一道 schema 校验，不让非法 step 落到 `test_steps`。

一个合法产物示例（"用户用合法手机号注册"反推结果）：

```json
{
  "case": {"name": "正向：新用户合法手机号注册（自动化）", "case_type": "web",
           "source": "ai_m8_auto", "source_case_id": 1234, "priority": 1, "tags": ["smoke"]},
  "steps": [
    {"step_order": 1, "step_name": "访问注册页", "step_type": "web_goto",
     "config": {"url": "${BASE_URL}/register", "timeout": 30}},
    {"step_order": 2, "step_name": "输入手机号", "step_type": "web_input",
     "config": {"by": "css", "locator": "#phone", "value": "13800001111", "clear_first": true}},
    {"step_order": 3, "step_name": "输入密码", "step_type": "web_input",
     "config": {"by": "css", "locator": "input[name='password']", "value": "Test@1234"}},
    {"step_order": 4, "step_name": "提交注册", "step_type": "web_click",
     "config": {"by": "text", "locator": "注册"}},
    {"step_order": 5, "step_name": "校验注册成功", "step_type": "web_assert_text",
     "config": {"by": "css", "locator": ".toast", "contains": "注册成功"}}
  ]
}
```

注意：step 2/3 的 selector（`#phone` / `input[name='password']`）是这套设计**唯一可能"编造"的地方**——这正是 §3 要解决的核心问题。

---

## §3 核心待定决策：selector 的事实来源从哪来？

这是 M8 能否产出"真能跑"的用例、而非"看着像但 selector 全错"的废稿的分水岭。M7 §9.2 已列了 4 路来源，本节把它们 + 一条新路（Playwright 实时抓 DOM）一起摆开权衡，给推荐，**请 owner 拍板**。

### 候选方案对比

| 方案 | selector 准确率 | 落地成本 | 依赖 | 适用 |
|---|---|---|---|---|
| **① Playwright 实时抓 DOM** | 高 | 中 | 测试环境页面可访问 | web，页面已就绪 |
| **② UI 截图多模态** | 低～中 | 低（M7 通道现成） | vision 模型 | web/app，无环境时兜底 |
| **③ 元素库 / Page Object** | 最高 | 高（需先沉淀） | PM 维护元素库 | 稳定回归资产 |
| **④ 录制脚本** | 最高 | 中（需录制工具） | Playwright codegen / 录制器 | 高保真，已有录制习惯 |

逐条说明：

**① Playwright 实时抓 DOM（新增方案，推荐作为 M8.0 主路）**
M8 反推前，先用 Playwright 打开 `business_steps` 推断出的目标 URL，对页面做一次"可交互元素快照"——抽取所有可见的 input/button/a/select 及其 `id / name / placeholder / text / aria-label / 稳定 css 路径`，压缩成一份"元素清单"喂给 AI。AI 不再凭空想 selector，而是**从真实存在的元素里挑**。
- 优点：selector 来自真实 DOM，准确率高；不需要 PM 提前沉淀任何东西，对存量需求零门槛；你已经有 Playwright adapter（`runners/web/adapters.py`），基础设施复用度高。
- 成本：要新增一个"页面快照"工具（一个 Playwright 脚本 + DOM 抽取逻辑），以及一个 `${BASE_URL}` → 具体页面路径的推断/配置；需要测试环境的页面可被平台访问（CI 网络可达）。
- 风险：登录态 / 多步表单的中间页难以一次性快照（需要按 step 序渐进式导航后再快照——M8.1 增强）。

**② UI 截图多模态（兜底，M7 通道现成）**
复用 `ai_case_drafts.ui_image_refs` 关联的截图 + vision 模型猜 selector。
- 优点：M7 已实装 `chat_with_images` / `ocr_extract`，零新基础设施；无测试环境也能跑。
- 缺点：截图推不出稳定的 css/xpath（模型只能猜 `按钮文案`、`字段 label`），准确率明显低于①；生成的 step 大多得标 `needs_review=true` 让 PM 校。
- 定位：作为①的 fallback——页面访问不了时退化到截图。

**③ 元素库 / Page Object（长期最优，但要先沉淀）**
新建 `page_objects` 表（M7 §9.2 已构想），PM 维护"页面 → 元素语义名 → selector"。AI 只做"业务步骤 → 元素语义名"的映射，selector 由元素库提供。
- 优点：准确率最高且稳定，selector 改一处全用例生效，天然支持自愈（元素库更新 → 用例不用重生）。
- 缺点：冷启动重——没有元素库就没法用；本质是把工作从"写用例"前移到"维护元素库"。
- 定位：M8.2 长期演进。①跑顺后，把①抓到的高频元素**自动沉淀**进 page_objects，逐步攒出元素库，避免纯手工维护。

**④ 录制脚本（高保真，看团队习惯）**
PM/QA 用 Playwright codegen 录一遍，AI 把录制脚本当 selector 底座 + functional 步骤当业务包装做参数化/断言注入。
- 优点：selector 是真人点出来的，最准。
- 缺点：要有录制习惯和上传管道；与"AI 自动生成"的初衷部分相悖（人还是得点一遍）。
- 定位：可选高保真通道，非主路。

### 推荐路线

**M8.0 先做①（Playwright 抓 DOM）为主 + ②（截图）兜底**，理由：
1. 对存量 functional 用例**零门槛**——不需要 PM 先建元素库或录脚本，落地最快、最能立刻体现"功能用例→可执行用例"的价值。
2. 复用现成 Playwright adapter，新增面小。
3. ①抓到的元素天然是③元素库的种子数据，为 M8.2 演进铺路，不走回头路。

③④作为 M8.2 / 可选通道，等①跑顺、有真实转换率数据后再投入。

> **请 owner 确认**：是否同意"M8.0 = ① Playwright 抓 DOM 为主 + ② 截图兜底"？这决定 §4 数据层是否要在 M8.0 就建 `page_objects` 表（若同意推荐路线，M8.0 **不建**，留到 M8.2）。

---

## §4 数据层改造（按推荐路线 M8.0）

### 4.1 Alembic 迁移 `m8_0001_auto_case.py`

`test_cases` 加列（对齐 M7 §9.1 草图）：

| 列 | 类型 | 说明 |
|---|---|---|
| `source_case_id` | `INT NULL FK test_cases.id ON DELETE SET NULL` | 自动化用例 → 来源 functional 用例 |
| `is_active` | `BOOLEAN DEFAULT true NOT NULL` | 同 source_case_id 下多版本时只执行 active 那份 |

索引：`(source_case_id, is_active)`。

`source` 列已存在（M7），M8 复用，新增取值 `ai_m8_auto`。

### 4.2 草稿表策略：**新建 `ai_auto_case_drafts`**

M7 §9.4 留了"复用 vs 新建"两案。鉴于自动化草稿的核心是**结构化 TestStep 行**（与 functional 的纯文本 `steps_text` 语义差异大），推荐**新建表**，更清晰：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `source_case_id` | int FK test_cases.id ON DELETE CASCADE | 来源 functional 用例 |
| `ai_run_id` | int FK ai_runs.id ON DELETE SET NULL | |
| `batch_id` | str(64) | |
| `model_label` | str(100) | |
| `target_case_type` | str(20) | M8.0 固定 `web` |
| `proposed_case` | JSON | `{name, case_type, priority, tags}` |
| `proposed_steps` | JSON | `[{step_order, step_name, step_type, config, assertion?}, ...]`，**即格式 B 的 step 数组** |
| `selector_source` | str(20) | `dom_snapshot` / `screenshot` / `page_object` / `recorded`，标每条来源便于追溯 |
| `needs_review` | bool default false | selector 靠猜（②）或有 todo step 时为 true |
| `unresolved_steps` | JSON nullable | 无法反推的 step 列表（`needs_ui_detail` 没兜住的），供 PM 补 |
| `status` | str(20) | `pending` / `accepted` / `rejected` |
| `committed_case_id` | int FK test_cases.id nullable | commit 后回填 |
| `created_at` / `updated_at` | datetime | |

索引：`(source_case_id, status)`、`batch_id`、`ai_run_id`。

> `ai_runs` 表零改动，复用现成 status 流转（pending/running/succeeded/failed）+ input_payload/output_payload。

---

## §5 后端

### 5.1 页面快照工具 `runners/web/dom_snapshot.py`（新文件，方案①核心）

```python
def capture_interactive_elements(url: str, *, timeout: float = 30,
                                 storage_state: dict | None = None) -> list[dict]:
    """
    用 Playwright 打开 url，抽取可见可交互元素，返回压缩清单：
    [
      {"tag": "input", "type": "tel", "id": "phone", "name": "phone",
       "placeholder": "请输入手机号", "text": "", "aria_label": "手机号",
       "css": "#phone", "candidate_by": "css", "candidate_locator": "#phone"},
      {"tag": "button", "text": "注册", "css": "button.submit",
       "candidate_by": "text", "candidate_locator": "注册"},
      ...
    ]
    """
```

实现要点：复用 `runners/web/adapters.py` 的 Playwright session；只抽 input/textarea/select/button/a/[role=button] 且可见的元素；为每个元素生成一个"最稳"候选 selector（优先 id > name > 唯一 text > 短 css path，避开自动生成的 hash class）；输出截断到 ~80 个元素防 token 爆。

### 5.2 上下文构建器 `server/services/auto_case_context_builder.py`（新文件）

```python
def build_auto_case_context(session, functional_case_id: int,
                            selector_source: str) -> AutoCaseContext:
    case = session.get(TestCase, functional_case_id)
    ctx = {
        "functional_case": {"name": case.name, "description": case.description,
                            "business_steps": case.business_steps,   # ← 核心输入
                            "tags": case.tags, "priority": case.priority},
        "requirement": build_requirement_context(session, case.requirement_id)
                       if case.requirement_id else None,             # 复用 M6
    }
    if selector_source == "dom_snapshot":
        target_url = _infer_target_url(case)        # 从 business_steps[0] / 项目 env 推
        ctx["dom_elements"] = capture_interactive_elements(target_url)
    elif selector_source == "screenshot":
        ctx["ui_images"] = [resolve_attachment(a) for a in _ui_refs(case)]  # 复用 M7
    return ctx
```

### 5.3 任务 handler `tasks/ai_tasks.py::_handle_auto_case_generation`

注册到 `_HANDLERS = {..., "auto_case_generation": _handle_auto_case_generation}`。

```
1. 读 ai_runs.input_payload = {source_case_id, target_case_type, selector_source, model_name, batch_id, user_prompt?}
2. ctx = build_auto_case_context(session, source_case_id, selector_source)
3. 读 prompt 模板 ai_gateway/prompts/auto_case_generation_v1.md
4. 替换 {{FUNCTIONAL_CASE}} / {{BUSINESS_STEPS}} / {{REQUIREMENT}} /
       {{DOM_ELEMENTS}}（dom_snapshot 分支）/ {{UI_IMAGE_HINTS}}（screenshot 分支）/
       {{STEP_TYPE_CONTRACT}}（把格式 B 的 step_type+config 契约整段塞进去，强约束输出）
5. resp = gateway.chat(prompt, model_config, response_format={"type":"json_object"})
       （screenshot 分支走 chat_with_images）
6. 解析 JSON → 得到 proposed_steps[]
7. **schema 校验每个 step**（见 5.4），非法 step 移入 unresolved_steps，needs_review=true
8. 插 ai_auto_case_drafts(status=pending, selector_source=..., proposed_steps=..., unresolved_steps=...)
9. ai_runs.output_payload = {batch_id, draft_id, step_count, unresolved_count}; status=succeeded
```

### 5.4 Step schema 校验 `server/services/auto_case_validator.py`（新文件，关键护栏）

入库前**必须**跑，确保格式 B 合法、可执行：

```python
WEB_STEP_TYPES = {"web_goto","web_click","web_input","web_select",
                  "web_wait","web_assert_text","web_screenshot","web_evaluate"}
BY_TYPES = {"css","xpath","id","name","class","text","link"}

def validate_web_step(step: dict) -> list[str]:
    errs = []
    st = step.get("step_type")
    if st not in WEB_STEP_TYPES: errs.append(f"未知 step_type={st}")
    cfg = step.get("config") or {}
    if st in {"web_click","web_input","web_select","web_assert_text"}:
        if cfg.get("by") not in BY_TYPES: errs.append("by 非法")
        if not cfg.get("locator"): errs.append("locator 为空")
    if st == "web_goto" and not cfg.get("url"): errs.append("goto 缺 url")
    if st == "web_input" and cfg.get("value") is None: errs.append("input 缺 value")
    if st == "web_assert_text" and not any(cfg.get(k) for k in ("equals","contains","regex")):
        errs.append("assert 缺 equals/contains/regex")
    return errs
```

> 这道校验是格式 B 的"守门员"——把执行期才会 `raise` 的错误前移到入库前拦截，保证落到 `test_steps` 的都是能跑的。

### 5.5 API 路由 `server/api/ai_auto_case.py`（新文件）

```
POST /api/ai/auto-case-generation        触发反推
     body: {functional_case_ids[], target_case_type="web",
            selector_source="dom_snapshot"|"screenshot", model_names[], user_prompt?}
     返回: {batches:[{batch_id, source_case_id, run_id, model_name}]}

GET  /api/ai/auto-case-drafts            列草稿（query: source_case_id? batch_id? status?）
GET  /api/ai/auto-case-drafts/{id}       详情（含 proposed_steps + unresolved_steps）
PUT  /api/ai/auto-case-drafts/{id}       编辑 proposed_steps（PM 修 selector）
POST /api/ai/auto-case-drafts/commit     批量入库：建 web TestCase + TestStep 行
     body: {draft_ids[]}
     事务: 逐 draft → validate 全部 step → 建 TestCase(case_type='web', source='ai_m8_auto',
            source_case_id=draft.source_case_id) → 批量建 TestStep → 旧 active 版本置 is_active=false
            → draft.committed_case_id 回填, status=accepted
GET  /api/ai/auto-case-generation/runs/{run_id}   查进度
```

注册：`server/api/__init__.py` 导出 + `server/main.py` router 循环加入（自动挂 `/api` 前缀）。

### 5.6 Prompt 模板 `ai_gateway/prompts/auto_case_generation_v1.md`（新文件）

骨架（关键是把格式 B 契约 + DOM 元素清单塞进去，强约束）：

```
你是资深 UI 自动化工程师。把下面的"功能用例业务步骤"逐步转换成可执行的 web 自动化步骤。

## 功能用例
{{FUNCTIONAL_CASE}}

## 业务步骤（逐条转换，保持 order 对齐）
{{BUSINESS_STEPS}}

## 页面真实可交互元素（只能从这里挑 selector，不要编造）
{{DOM_ELEMENTS}}

## 输出契约（严格遵守，否则用例无法执行）
- step_type 只能用: web_goto / web_click / web_input / web_select / web_wait /
  web_assert_text / web_screenshot / web_evaluate
- config.by 只能用: css / xpath / id / name / class / text / link
- 各 step_type 的 config 字段：
  {{STEP_TYPE_CONTRACT}}
- step_type_hint → step_type 默认映射: navigate→web_goto, input→web_input,
  select→web_select, submit→web_click, verify→web_assert_text, wait→web_wait
- 若某业务步骤在元素清单里找不到对应元素：**不要编 selector**，
  输出 {"step_order": i, "step_type": "todo", "reason": "未在页面找到 xxx"}，
  系统会归入 unresolved_steps 让人工补

## 输出（严格 JSON）
{"case": {"name": "...", "case_type": "web", "priority": 0|1|2|3, "tags": [...]},
 "steps": [{"step_order": 1, "step_name": "...", "step_type": "...", "config": {...}}, ...]}
```

---

## §6 前端（M8.0 最小集）

| 组件 | 路径 | 职责 |
|---|---|---|
| 入口按钮 | `pages/cases/CasesTab.tsx` | functional 用例行加"生成自动化用例"按钮（仅 case_type=functional 显示）；列表多选后批量触发 |
| `AutoCaseLauncherDialog.tsx` | `pages/cases/dialogs/` | 选 model / target_case_type(M8.0 仅 web) / selector_source(DOM 抓取 \| 截图) / user_prompt → 触发 |
| `AutoCaseReviewDialog.tsx` | `pages/cases/dialogs/` | 渲染 proposed_steps（按 step 表格）；`needs_review` / `unresolved_steps` 高亮；行内可编辑 selector；右下"入库"按钮 |
| `CasesTab` 微调 | 同上 | `source='ai_m8_auto'` 用例加徽标"AI 自动化"；列出 source_case_id 链回 functional 用例 |

入库后用例进标准 web 用例列表，**直接走现有 `POST /api/run_test` 一键执行**——这就是用户要的"一键点击执行"。

---

## §7 任务拆分（commit 前缀 `feat(ai-m8):`）

| # | Task | 类型 | 关键文件 |
|---|---|---|---|
| 1 | 迁移：test_cases.source_case_id/is_active + ai_auto_case_drafts 表 + 模型类 | backend schema | `m8_0001_auto_case.py`、`database/models/ai_auto_case_draft.py`、`test_case.py`、`__init__.py` |
| 2 | DOM 快照工具 | backend | `runners/web/dom_snapshot.py` |
| 3 | 上下文 builder | backend | `server/services/auto_case_context_builder.py` |
| 4 | Step schema 校验器 | backend | `server/services/auto_case_validator.py` |
| 5 | Prompt 模板 v1 | backend | `ai_gateway/prompts/auto_case_generation_v1.md` |
| 6 | Task handler + 注册 | backend | `tasks/ai_tasks.py` |
| 7 | Service（含 commit 事务 + is_active 翻版） | backend | `server/services/ai_auto_case_service.py` |
| 8 | API 路由 + deps | backend | `server/api/ai_auto_case.py`、`__init__.py`、`main.py` |
| 9 | 前端 api.ts + types | frontend | `frontend/src/lib/api.ts`、`types/domain.ts` |
| 10 | Launcher / Review 组件 + CasesTab 接线 | frontend | `pages/cases/dialogs/`、`CasesTab.tsx` |
| 11 | E2E smoke（见 §9） | docs | 本文档 |

---

## §8 关键复用点

- **`runners/web/adapters.py`** Playwright session + `BY_TYPES` —— DOM 快照与执行同源，selector 契约一致
- **`runners/steps/web_actions.py`** 8 个 web runner —— 执行侧零改动，M8 只负责产出合法 config
- **M7 `test_cases.business_steps` / `requirement_id`** —— 反推主输入，不反查 draft
- **M6/M7 `ai_runs` + `AiModelConfig` + `gateway.chat/chat_with_images`** —— 任务编排与模型路由全复用
- **`build_requirement_context`** —— 需求上下文复用
- **`POST /api/run_test`** —— 产物直接走现有执行链路，无需新执行入口

## §9 Verification

### 后端 smoke

```bash
alembic upgrade head
# 0. 准备一条 M7 落地的 functional 用例（带 business_steps），假设 case_id=1234，
#    对应一个可访问的注册页 BASE_URL/register

# 1. DOM 快照工具单测
python -c "from runners.web.dom_snapshot import capture_interactive_elements as c; \
import json; print(json.dumps(c('http://127.0.0.1:5173/register'), ensure_ascii=False, indent=2))"
# 期望: 列出 #phone / #password / 注册按钮 等真实元素 + candidate selector

# 2. 触发反推（DOM 抓取路）
curl -X POST http://127.0.0.1:54351/api/ai/auto-case-generation \
  -H 'Content-Type: application/json' \
  -d '{"functional_case_ids":[1234],"target_case_type":"web",
       "selector_source":"dom_snapshot","model_names":["gpt-4o"]}'
# 期望: 1 个 batch + run_id

# 3. 轮询 run
curl http://127.0.0.1:54351/api/ai/auto-case-generation/runs/<run_id>
# 期望: succeeded, output_payload.step_count>0, unresolved_count 合理

# 4. 看草稿
curl 'http://127.0.0.1:54351/api/ai/auto-case-drafts?source_case_id=1234' | jq
# 期望: proposed_steps 每条 step_type∈8种, config.by∈7种, locator 来自真实 DOM 元素

# 5. 入库
curl -X POST http://127.0.0.1:54351/api/ai/auto-case-drafts/commit \
  -d '{"draft_ids":[<draft_id>]}'
# 期望: 新建 test_cases(case_type='web', source='ai_m8_auto', source_case_id=1234) + N 条 test_steps

# 6. 一键执行（验证产物真能跑）—— 这是 M8 成败的终极判据
#    /api/run_test 实际契约（database/schemas/run_test_request.py）：
#    {project, module?, category?, case, device_id?} —— case 是单个用例 id
curl -X POST http://127.0.0.1:54351/api/run_test \
  -H 'Content-Type: application/json' \
  -d '{"project":<pid>, "case":<new_web_case_id>}'
# 期望: TestReport 正常跑完，不卡在 running；step 执行结果可见
```

### 校验器单测

- 合法 web step → `validate_web_step` 返回 `[]`
- `by="invalid"` / `locator=""` / `web_input` 缺 `value` / `web_assert_text` 三断言全空 → 各自报对应错
- 含 `step_type=todo` 的 draft → 入库时该 step 归入 unresolved_steps，不写 test_steps，draft.needs_review=true

### 联动验证

- 同一 functional 用例反推两次 → 第二次入库时第一份 `is_active=false`，执行列表只显示最新
- functional 用例的 `business_steps[i].needs_ui_detail=true` 且 DOM 里找不到元素 → 对应 step 进 unresolved_steps（验证 §3 的质量传递）
- screenshot 路：选无测试环境的页面 + 截图 → selector_source='screenshot'，needs_review=true 比例高
- **闭环终判**：M7 生成 functional → commit → M8 反推 → commit → run_test 跑通，全程不手写一行 selector

### 每步通过

```bash
cd frontend && npm run typecheck && npm run lint
python -m compileall server tasks database ai_gateway runners
alembic upgrade head
```

---

## §10 Scope（Out of M8.0）

- `page_objects` 元素库（方案③）与录制脚本（方案④）→ M8.2
- API 用例反推（`case_type=api`，OpenAPI/Swagger 路）→ M8.1，复用本套框架换 step_type=http_request
- 多步表单/登录态的渐进式 DOM 快照 → M8.1
- selector 自愈（执行失败时 AI 重定位）→ M9
- DOM 快照元素自动沉淀进 page_objects（①→③演进）→ M8.2

## §11 待 owner 拍板清单

1. **§3 selector 来源**：是否采纳"M8.0 = ① DOM 抓取主路 + ② 截图兜底"？（默认推荐）
2. **草稿表**：新建 `ai_auto_case_drafts`（推荐）还是复用 `ai_case_drafts` 加 `kind` 列？
3. **目标 case_type 范围**：M8.0 是否只做 `web`，把 api/android/ios 留后续里程碑？（推荐只做 web，先打通一条）
4. **`${BASE_URL}` / 目标页 URL 来源**：从 `test_environments` 表取，还是触发时手填？（影响 §5.2 `_infer_target_url`）
