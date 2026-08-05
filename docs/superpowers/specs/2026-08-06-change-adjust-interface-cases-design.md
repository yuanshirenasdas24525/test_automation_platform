# 变更调整：由接口变更驱动的用例增改删

- 日期：2026-08-06
- 状态：设计已认可，待 spec 复核
- 取代：原「模块大纲」功能（align / replan / purge 那套整体移除）

## 1. 背景与目标

当某个接口发生调整、新增或删除时，测试人员需要同步维护该模块下的接口用例。现状是重新走「生成用例」全量向导，无法针对"这次变更"增量处理。

**目标**：在「AI 生成接口用例」抽屉里，把原「模块大纲」Tab 改造为「变更调整」Tab。用户描述本次变更（可附接口文档），AI 产出一份**用例级调整大纲**（新增/修改/删除哪几条用例），用户审阅确认后，**直接写入真实模块用例**。

**非目标**：不再保留模块大纲的测试点（point）/对齐（align）/缺口清理（purge）机制。

## 2. 用户流程

两阶段：规划 → 审阅 → 应用。

1. 「变更调整」Tab 表单：
   - `本次变更 / 新增需求`（textarea，必填）
   - `接口文档`（文件选择，接受 `.json/.yaml/.yml/.doc/.docx/.pdf/.md`，可多选，可选）
   - `接口文档链接`（textarea，多个换行或逗号分隔，可选）
   - `模型`（下拉）
   - 「规划调整」按钮
2. 后端产出**调整大纲**（不写库），前端渲染。
3. 用户审阅调整大纲：按 新增 / 修改 / 删除 分组带色标；**删除项逐条带勾选框（默认勾选）**，可取消勾选以跳过该删除。
4. 「应用变更」→ 写入真实用例，返回增改删统计 + 逐条失败列表，前端刷新模块用例。

## 3. 后端设计

### 3.1 文档解析 `server/services/doc_ingest.py`（新增）

统一入口，产出一份"接口结构上下文"文本 + 结构化端点列表：

- `.json` / `.yaml` / `.yml`：
  - 先尝试**结构化解析 OpenAPI / Swagger**：抽出每个 endpoint 的 `method` `path` `summary` `参数` `请求 body schema` `响应 schema`。
  - Postman collection：本期"能识别就抽 request 列表（method/url/body），识别不了则回退纯文本"；完整 Postman 支持留下期。
  - 结构化失败则回退到纯文本抽取。
- `.pdf` / `.docx` / `.doc` / `.md`：复用现有 `server/services/doc_parser.py` 抽文本。
- **链接**（本期做）：后端抓取 URL；若响应是 openapi json/yaml 则结构化，否则取页面/正文文本。
  - SSRF 防护：限制 http/https、拒绝内网/环回地址、设超时与大小上限。

输出：`ParsedInterfaceContext { endpoints: [...], text_blocks: [...], warnings: [...] }`。单个文件/链接失败只记 warning 并跳过，不中断整体。

### 3.2 规划编排 `server/services/change_plan_service.py`（新增）

- `plan_preview(module_id, mode, model_name, change_text, parsed_context) -> ChangePlan`：
  - 载入当前模块现有接口用例：`{id, name, method, path 签名, 简要}`。
  - 组 prompt：`本次变更` + 解析出的接口结构上下文 + 现有用例清单 → 要求 AI 返回 JSON 调整大纲。
  - **调整大纲 op 结构**：
    ```
    { id: number,                // 该 op 在本 plan 内的稳定序号，apply 用它勾选
      action: "add" | "modify" | "delete",
      target_case_id?: number,   // modify/delete 必填，指向现有用例
      title: string,             // 用例/测试点标题
      endpoint?: { method, path },
      reason: string }           // 为什么增/改/删
    ```
  - AI 返回非法 JSON → 走既有修复/重试策略。
  - 产出的 `ChangePlan { plan_id, ops[], context_digest }` **持久化为一条 `ai_run`**（`output_payload` 存 ops + 解析上下文），`plan_id` 即 run id，短期复用，避免应用阶段二次上传/解析。
- `plan_apply(plan_id, selected_op_ids, confirmed_delete_ids) -> ApplySummary`：
  - `add` / `modify`：复用现有 `ai_generate_batch` 的详情生成 + `_harden_generated_cases` 契约加固，产出带请求/断言的完整接口用例 →
    - `add`：在模块内新建用例。
    - `modify`：对 `target_case_id` **重新生成完整详情并整条覆盖**，保留原 case id / batch 归属（不做字段级 merge）。
  - `delete`（仅 `confirmed_delete_ids` 内的）：真删对应真实用例。
  - **逐条容错**：单条失败转入 `errors[]`，不回滚整批（对齐现有 `batch_mark` 模式）。
  - 返回 `{ added, modified, deleted, errors[] }`。

### 3.3 路由 `server/api/change_adjust.py`（新增）

- `POST /change_plan/preview`（multipart：change_text、model、files[]、links、module_id、mode）→ `ChangePlan`
- `POST /change_plan/apply`（json：plan_id、selected_op_ids、confirmed_delete_ids）→ `ApplySummary`
- 对象级授权：按 `module → project` 调 `assert_project_access`（见 CLAUDE.md 反 IDOR 约定）。
- 挂载：`server/api/__init__.py` 导出 + `server/main.py` 循环里加入。

### 3.4 移除（原模块大纲）

确认仅被旧 `ModuleOutlinePanel` 引用后移除：
- 端点：`module_outline` 的 `get` / `align_preview` / `apply` / `purge_gaps` / `replan_preview` / `replan_apply`（`server/api/functional_cases.py`）。
- 服务：`server/services/module_outline_service.py`。
- 前端 `moduleOutlineApi` 及相关 domain 类型（`OutlineAlign*` / `OutlineReplan*`）。
- 保留：`ai_generate_outline`（属「生成用例」流程，不动）。

## 4. 前端设计

- 重写 `frontend/src/components/case/module-outline-drawer.tsx`（或改名 `change-adjust-panel.tsx`）：
  - 表单四项（已完成 UI 骨架）对接新端点；文件走 multipart。
  - 「规划调整」→ `change_plan/preview` → 渲染调整大纲。
  - 调整大纲：按 action 分组、色标；删除项 checkbox（默认选中）。
  - 「应用变更」→ `change_plan/apply(plan_id, 选中 ops, 确认删除 ids)` → toast 统计 + `onApplied` 刷新。
- Tab 标签 `模块大纲` → `变更调整`（已完成）。
- 新增 domain 类型：`ChangeOp` / `ChangePlan` / `ApplySummary`。
- API 客户端：新增 `changeAdjustApi.preview/apply`（`src/lib/api.ts`）；移除 `moduleOutlineApi`。

## 5. 错误处理

- 文档/链接解析：逐源容错，warning 汇总回前端展示。
- AI JSON 非法：既有修复/重试；仍失败则报错，preview 不产出 plan。
- 应用阶段：逐条 op 容错，返回 `errors[]`，前端提示"部分成功"。
- 链接抓取：超时/大小/内网防护，失败按 warning 跳过。

## 6. 测试与验证

平台无传统单测框架，以手动验证为主：
- 结构化解析：对样例 OpenAPI json/yaml 校验抽出的 endpoints（可加轻量本地脚本 `python -m ...` 自查）。
- 端到端：样例变更文本（含"新增 X 接口 / 修改 Y / 删除 Z"）→ preview 产出对应 add/modify/delete ops → apply 后库中用例正确增改删，删除仅作用于已确认项。
- 授权：非本项目用户访问 preview/apply 返回 403。

## 7. 本期范围决策（YAGNI）

- ✅ 链接抓取：本期做（含 SSRF 防护）。
- ✅ OpenAPI / Swagger 结构化解析：本期做。
- ◑ Postman collection：本期"能识别就结构化、否则回退纯文本"，完整支持下期。
- ❌ 模块大纲测试点机制：移除，不迁移。
