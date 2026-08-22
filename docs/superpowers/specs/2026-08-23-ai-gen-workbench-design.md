# AI 用例生成工作台重做 — 设计 Spec

日期：2026-08-23 · 状态：已评审，待实现
预览：`gen_workbench_mockup`（Artifact）

## 背景与目标

当前「AI 生成用例」是一个一次性抽屉：需求→选模型/覆盖→生成大纲→审阅→生成→写入，各步之间靠翻页切换，**生成完再回去重新生成/看大纲/看审阅要来回跳、状态会丢**；且是**黑盒**，用户看不到 AI 怎么生成、也不知道该准备/检查什么。

本次重做三件事（合成一个交付）：

- **#3 重做生成 UI（容器）**：保持**抽屉**形式，但改成**宽抽屉、左右两栏、状态常驻**——右栏大纲+用例+查缺补漏同屏增量迭代，不再翻页丢状态。
- **#1 提示词 / 流程预览（只读）**：让用户看到本次真实渲染的提示词 + 生成流程，黑盒变白盒。
- **#2 功能测试要点 Checklist（要点 + 覆盖状态）**：针对**当前被测功能**列出"该测哪些方面"，每方面标已覆盖几条，快速熟悉功能 + 暴露缺口 + 一键查缺补漏。

非目标（本期不做）：提示词可编辑/持久化（只读即可）；接口/Android/iOS 生成 UI 的同款改造（本期只做功能用例这条；结构预留复用）；成员管理相关功能。

## 术语

- **大纲点 / 测试点（outline point）**：`{title, category}`，`ai_generate_outline` 产出。
- **用例（case）**：`ai_generate_batch` 从测试点生成的详细用例。
- **看板项（board item）**：前端把「测试点」与「其生成的用例」合并成一个可增量操作的对象，带状态：`待生成 / 已生成 / 已入库`。

## 关键设计决策（含两个开放点的取舍）

1. **形态**：宽抽屉（屏宽 ~68–82%），左右两栏。左=配置+透明度，右=常驻看板。移动端降级为单列。
2. **不用会丢状态的 Tab**：大纲/用例/查缺补漏都在右栏同一块看板上，靠**看板项状态**表达阶段，顶部一条只读的阶段进度条（需求✓ · 大纲N · 生成M/待K · 入库X）。
3. **提示词预览 = 只读**：展示本次**已渲染**的提示词（不是模板占位）+ 生成流程图文（大纲→四层过滤→batch）。不提供编辑。
4. **功能 Checklist = 要点 + 覆盖状态**，数据来源（**开放点取舍**）：
   - **v1 由一次轻量 AI 调用产出**（新 prompt `feature_checklist`）：输入 = 需求/digest + 本模块**已有用例名清单**；输出 = `aspects[]`，每项 `{aspect, what_to_test, covered_case_names[], coverage: covered|thin|none}`。即**AI 同时完成"归纳该测哪些方面"与"把已有用例映射到各方面"**，一步到位、接地、每次新鲜。
   - 不做独立的关键词分类器（YAGNI）；不预置各功能模板（登录/CRUD/列表…）——预置模板留作 v2 增强项。
   - "偏薄/未覆盖"阈值：`covered=0 → none`；`1–2 条 → thin`；`≥3 → covered`（阈值先硬编码，可配置留后）。
5. **一键查缺补漏对接**：Checklist 里 `thin/none` 的方面，点击 → 以该方面为约束调用现有 `ai_outline_gaps`（把方面名/what_to_test 拼进 `contract`），补出的点追加到看板并高亮。

## 架构与组件

### 后端（3 个只读/轻量端点，全部复用已有能力）

| 端点 | 方法 | 作用 | 复用 |
|---|---|---|---|
| `/api/functional_cases/ai_prompt_preview` | POST | 返回本次**已渲染**的提示词（outline & batch 两段）+ 流程步骤描述 | `_load_prompt` + 各 `render_*_placeholders`；**不调 LLM** |
| `/api/functional_cases/ai_feature_checklist` | POST | 返回 `aspects[]`（要点+覆盖） | 新 prompt `feature_checklist` + `_resolve_model` + `chat_markdown`；读 `_existing_case_names` |
| （已有）`ai_generate_outline` / `ai_generate_batch` / `ai_outline_gaps` | — | 大纲/用例/补漏 | 本次已带四层接地过滤，无需改 |

- `ai_prompt_preview` 入参：`module_id, mode(functional|interface), coverage, dimensions, requirement_text?`。渲染时用与真实生成**同一套** placeholder 组装，保证"所见即所用"。功能模式复用 outline/batch 的 placeholder 组装逻辑（抽成可复用函数，避免和真实生成漂移）。
- `ai_feature_checklist` 入参：`module_id, requirement_text/digest, model_name`。输出 JSON `{aspects:[{aspect, what_to_test, covered_case_names, coverage}]}`。截断容错复用 `_salvage_json_objects`。
- 新 prompt `ai_gateway/prompts/feature_checklist.md`：产出"该功能测试要点 + 把已有用例映射到要点"，**只归纳真实存在的能力**（复用能力边界口径，不臆造第三方登录等）。

### 前端（重做生成抽屉）

现状：生成流程内嵌在 `FunctionalCasesPage.tsx`（5000+ 行，`aiGenOpen` 控制）。**问题**：该文件过大、生成逻辑与列表页耦合。**本次**：把生成抽屉抽成独立组件树，降低耦合、便于维护。

新组件（`frontend/src/components/case/ai-gen/`）：

- `AiGenDrawer.tsx`：宽抽屉容器 + 阶段进度条 + 左右两栏布局 + 生成会话状态（board items、filter stats、checklist、prompt preview）。**状态常驻**：关键状态提升到这里，切换操作不丢。
- `GenConfigPanel.tsx`（左上）：需求&素材、模型、覆盖、维度。
- `FeatureChecklistPanel.tsx`（左中，#2）：调 `ai_feature_checklist`，渲染要点+覆盖，缺口项「一键查缺补漏」。
- `PromptPreviewPanel.tsx`（左下，#1）：调 `ai_prompt_preview`，只读展示提示词+流程；可折叠。
- `GenBoard.tsx`（右栏）：工具条（生成大纲/查缺补漏/生成选中/全选入库）+ 四层过滤统计条 + 看板项列表（`BoardItemRow`：优先级/类别/状态/展开用例/单条重生成/入库）。查缺补漏新增项高亮。

数据流：抽屉持有 `sessionState = { requirement, config, points[], casesByPoint, filterStats, checklist }`。各操作只**增量更新**该状态，UI 不卸载。写入库仍走 `functionalCasesApi.create`（已带 priority）。

### 隔离与可测

- 后端两个新端点是**纯读/单次 LLM**，无副作用（不落库），可独立验证。
- `feature_checklist` 的解析/覆盖归类是纯函数，抽到 service 便于单测。
- 前端按面板拆分组件，各自 props 明确、可独立理解。

## 错误处理

- `ai_prompt_preview` 不调 LLM，失败只可能是模块/prompt 缺失 → 404/500，前端面板显示"预览不可用"。
- `ai_feature_checklist` LLM 失败/解析失败 → 返回空 aspects + `warning`，前端显示"暂无法生成要点清单，可重试"，**不阻断**生成主流程。
- 截断 → salvage 抢救已完整 aspects。

## 测试

- 后端：`feature_checklist` 解析/覆盖归类纯函数单测（覆盖 covered/thin/none 阈值、salvage）；`ai_prompt_preview` 渲染快照测试（占位符全部替换、无 `{{}}` 残留）。
- 端到端：真实触发 `ai_feature_checklist`（登录模块），核对 aspects 接地、覆盖数与实际用例一致；`ai_prompt_preview` 返回的提示词与真实生成用的一致。
- 前端：手动走查——生成大纲→查缺补漏→生成→入库全程状态不丢、不翻页。

## 实施分期（可增量交付验证）

- **P1 后端**：`ai_prompt_preview` 端点 + `feature_checklist` prompt/端点/纯函数 + 单测。真实验证。
- **P2 前端骨架**：`AiGenDrawer` 宽抽屉两栏 + 阶段条 + 常驻 sessionState；把现有生成/大纲/审阅逻辑迁入右栏看板（不丢功能）。
- **P3 左栏面板**：FeatureChecklist + PromptPreview 接入两个新端点。
- **P4 打磨**：过滤统计条、查缺补漏高亮追加、单条重生成、移动端降级；构建 `npm run build` 验证。

每期落地即合并主树、`npm run build`，可在 54351 验证。

## 复用到 API / Android / iOS（后续）

`ai_prompt_preview` 已按 `mode` 参数化；`AiGenDrawer` 的 board/config/preview 结构与 case_type 无关，接口用例（interface 模式）可最先复用；Android/iOS（web_ui 生成）走 `WebUiCaseGenerationDialog`，本期不动，结构对齐后续再并。
