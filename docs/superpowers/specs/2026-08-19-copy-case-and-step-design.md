# 用例 / 步骤复制功能设计

日期：2026-08-19
分支：`claude/copy-feature-test-cases-64e199`

## 目标

给测试用例平台加一个「复制—粘贴」能力，覆盖 **功能(functional) / API / WEB / ANDROID / IOS** 全部用例类型，支持两个层级：

- **用例级**：在用例列表里选中一条用例，`Ctrl+C` 复制，`Ctrl+V` 在选中行下方插入一份副本。
- **步骤级**：在用例编辑器（CaseDialog / step-editor）里选中一个步骤，`Ctrl+C` 复制，`Ctrl+V` 在选中步骤下方插入一份副本。

复制成功时，被复制的行有「边缘流动光效」绕行一圈作为反馈。

## 交互规则（来自需求原文）

1. 选中一条用例或一个步骤，`Ctrl+C` → 复制成功，该行边缘出现流动光效绕行一圈。
2. `Ctrl+V` → 在选中的用例 / 步骤**下方**插入之前复制的内容（对称：复制用例→粘贴用例；复制步骤→粘贴步骤）。
3. 没有选中任何行时，按 `Ctrl+V` 无效（no-op）。
4. 聚焦了对应区域但没有选中具体行时，`Ctrl+V` 插入到该区域的**第一条**。
5. **不能跨类型**：
   - 用例：API 用例只能粘贴进 API 列表，web/android/ios/functional 同理，各自隔离。
   - 步骤：按平台组隔离——`web` 步骤只能粘贴进 web / mixed 用例；`app` 步骤只能粘贴进 android / ios 用例；`api`（http_request）步骤只能粘贴进 api / mixed 用例。类型不匹配时 `Ctrl+V` 无效。

## 关键决策（已与用户确认）

- **粘贴语义对称**：复制什么就粘贴什么。剪贴板同一时刻只持有一项（一条用例 或 一个步骤）。
- **剪贴板全局共享**：内存单例，跨对话框、跨列表页有效；刷新页面后清空（不持久化到 localStorage）。步骤跨用例复用是核心场景。
- **步骤也按类型隔离**：剪贴板记录被复制项的 `case_type` / 步骤平台组，粘贴时校验。
- **用例副本命名**：追加 `_副本` 后缀（`登录测试` → `登录测试_副本`），若已存在则 `_副本2`、`_副本3`…
- **用例级复制走前端 clone**，不新增后端接口：`GET /api/test_cases/{id}` 拿带 steps 的详情 → 组装 `TestCaseCreate`（改名）→ `POST /api/test_cases`。复用现有创建 / 排序接口。
- **光效为一次性**：绕行一圈（约 1.2s）后自动结束，不持续循环。

## 架构

### A. 全局剪贴板（`src/lib/case-clipboard.ts` 或 Context）

一个内存单例，暴露读写和订阅：

```ts
type ClipboardItem =
  | { kind: 'case'; caseType: CaseType; snapshot: TestCaseDetail }
  | { kind: 'step'; platformGroup: 'web' | 'app' | 'api'; snapshot: TestStepDraft };

// get(): ClipboardItem | null
// set(item): void
// subscribe(cb): unsubscribe   // 供 UI 反映「剪贴板里有没有东西」（可选）
```

用 React Context + `useSyncExternalStore`（或轻量 `useState` + module 单例）实现，保证跨页面共享。**in-memory only**。

`platformGroup` 推导：由步骤 `step_type` 前缀决定——`web_*` → `web`，`app_*` → `app`，`http_request` → `api`，`sleep`/`assert` 等通用步骤归入复制来源用例的组（即随源用例 case_type 归组，避免通用步骤无法粘贴）。

### B. 选中态 / 聚焦态（复制粘贴锚点）

现有用例行的 `selected: Set<number>` 是**多选**（批量删/跑），语义不同，不复用。新增独立的「当前聚焦行」概念：

- **用例列表**（`ApiCaseTable` / functional 列表行）：新增 `activeCaseId: number | null`。点击行主体标记 active，视觉高亮（不影响 checkbox 多选）。
- **step-editor**：新增 `activeStepIndex: number | null`。点击步骤行标记 active，视觉高亮。
- **区域聚焦**：列表容器 / step-editor 容器需要能接收键盘事件。给容器加 `tabIndex`，或用挂在容器上的 `keydown` 监听 + 判断事件源是否落在该区域内。`activeXxx == null` 但区域聚焦 → 粘贴到第 0 位。

### C. 键盘事件

在各承载区域（用例列表容器、step-editor 容器）挂 `keydown`：

- `Ctrl+C`（或 `Cmd+C`）且 active 项存在，且事件不是发生在输入框 / textarea / contenteditable 里（避免抢正常文本复制）→ 执行复制 + 触发光效。
- `Ctrl+V`（或 `Cmd+V`）同样避开输入框 → 执行粘贴校验与插入。
- 判断 `e.key === 'c' | 'v'` 且 `e.ctrlKey || e.metaKey`。

### D. 复制逻辑

- **用例**：`activeCaseId` → 调 `casesApi.get(id)` 或已有详情缓存拿 `TestCaseDetail` → `clipboard.set({ kind:'case', caseType, snapshot })`。触发该行光效。
- **步骤**：`activeStepIndex` → 取 `value[index]`（`TestStepDraft`）→ 推导 `platformGroup` → `clipboard.set({ kind:'step', platformGroup, snapshot })`。触发该步骤行光效。

### E. 粘贴逻辑

先做 **guard**（任一不满足即 no-op，轻量 toast 提示原因）：

1. 剪贴板非空；
2. `kind` 与当前上下文匹配（用例列表只接受 `kind:'case'`；step-editor 只接受 `kind:'step'`）；
3. 类型匹配（见交互规则 5）。

通过后：

- **用例粘贴**：由 `snapshot` 组装 `TestCaseCreate`（去掉 id / 主键类字段、清 latest_run 等运行态、名称加 `_副本` 去重）→ `POST` 新建 → 用现有 `insertRowAbove` / `reorder` 基础设施把新用例排到 active 行下方（无 active 则置顶）→ 刷新列表。
- **步骤粘贴**：深拷贝 `snapshot`，重置 `id`（新步骤无 id）→ 插入到 `activeStepIndex + 1`（无 active 则 index 0）→ `onChange` 同步 `step_order`。

### F. 流动光效组件

可复用的 CSS 效果，用例行和步骤行共用：

- 实现：一层绝对定位的伪元素 / 覆盖层，用 `conic-gradient` 或沿边缘运动的 `linear-gradient`，配合 `@keyframes` 做「亮点绕四条边跑一圈」，`animation` 单次（`forwards` / 结束后由状态移除）。
- 触发：复制成功时给目标行设一个短时的 `copiedFlashId`（用例）/ `copiedFlashIndex`（步骤），约 1.2s 后清除，class 随之移除。
- 尊重 `prefers-reduced-motion`：该场景下降级为一次静态描边高亮。

## 数据流

```
用户点击行 → setActiveCaseId / setActiveStepIndex（高亮）
  ↓ Ctrl+C
读取 active 项快照 → clipboard.set(...) → 触发光效
  ↓ Ctrl+V（另一处或原处）
guard(kind/type) 通过
  ├─ 用例：GET 详情已在快照 → 组装 TestCaseCreate(改名) → POST → 排序到下方 → invalidate
  └─ 步骤：深拷贝快照 → 插入 activeStepIndex+1 → onChange
```

## 错误处理

- 粘贴 guard 不通过：静默或 `sonner` toast 一句「不能跨类型粘贴」/「没有可粘贴的内容」，不抛错。
- 用例 clone 的 `POST` 失败：`ApiError` → toast 报错，列表不变。
- 复制时详情拉取失败：toast 报错，剪贴板不写入，不触发光效。
- 名称去重：`_副本` 冲突时递增数字，前端基于当前模块用例名集合判断；即便撞后端唯一约束，POST 失败也有 toast 兜底。

## 测试策略

平台无传统前端单测。验证方式：
- 手动端到端：在 web dev server（`npm run dev`，注意改前端要 `npm run build` 才能从 54351 后端端口看到——见 CLAUDE.md trap）逐类型验证复制/粘贴/跨类型拦截/光效/置顶/下方插入/无选中无效。
- 关键纯函数（名称去重、`platformGroup` 推导、`TestCaseCreate` 组装）可抽成可单独调用的工具函数，便于 review 与将来补测。

## 不做（YAGNI）

- 不做多选批量复制（只复制单个 active 项）。
- 不持久化剪贴板到 localStorage / 后端。
- 不做系统剪贴板互通（不写 `navigator.clipboard`），纯应用内内存剪贴板。
- 不新增后端 duplicate 接口。
- 不支持跨类型「智能转换」步骤。

## 涉及文件（预估）

- 新增：`frontend/src/lib/case-clipboard.ts`（剪贴板单例/Context）
- 新增：光效组件 / CSS（`frontend/src/components/ui/` 下，或一个 `copy-flash` 工具）
- 改：`frontend/src/pages/AutomationCasesPage.tsx`（active 行 + 键盘 + 用例复制粘贴）及其中的 `ApiCaseTable`
- 改：`frontend/src/pages/FunctionalCasesPage.tsx`（同上，functional 只用例级）
- 改：`frontend/src/components/case/step-editor.tsx`（active 步骤 + 键盘 + 步骤复制粘贴）
- 可能改：`frontend/src/components/case/CaseDialog.tsx`（把区域聚焦 / 键盘上下文串起来）
