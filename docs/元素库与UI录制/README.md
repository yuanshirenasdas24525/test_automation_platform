# 元素库与 UI 录制专题文档

> 整理日期：2026-08-11
> 当前状态：主需求已更新为 v1.7；M0～M4 代码闭环、Web 默认离线交互、F12 式元素定位与 AI 安全探索已实现，Web/Android 已完成真实环境验收，iOS 待宿主机模拟器环境验收

本目录统一保存项目“元素库、页面快照、元素拾取、UI 录制”相关的历史方案、决策记录和样式草稿，避免资料散落在 `docs/` 根目录。

## 一、正式基线文档

### 0. [UI 录制中心实施计划 v1.0](./UI录制中心-实施计划-v1.0.md)

2026-08-07 根据最终确认范围建立的实施基线，明确：

- Web 采用可离线运行业务流程的资源归档与 XHR/Fetch 本地 Mock；
- Android/iOS 首期只支持模拟器；
- 断言复用现有封装；
- M0～M4 里程碑、数据域、API 契约和验收标准。

### 1. [UI 录制中心需求文档与实施方案](./UI录制中心-需求文档与实施方案.md)

2026-07-17 开始编写，2026-08-12 更新为 v1.7 实施基线，包含：

- Web / Android / iOS UI 录制；
- 页面快照、元素树、动作时间线；
- FR-11 项目元素库；
- FR-16 页面漫游视图；
- FR-17 步骤编辑器元素选择器；
- FR-18 用例页入口、紧凑悬浮录制条和独立浏览器窗口；
- FR-19～FR-22 屏幕、Console、XHR/Fetch、用户事件、环境信息、步骤关联及上下文结果页；
- FR-23 默认离线交互、F12 式元素定位、离线 Portal 关闭兜底与 Web AI 安全探索补录；
- 录制态与正式执行态共用 `UIContextCollector`；
- `ui_elements`、`ui_element_occurrences`、`ui_element_locators` 等数据模型；
- Recorder、快照处理、定位器生成、AI 用例衔接和分阶段实施计划。

### 2. [UI 录制中心决策记录 ADR](./UI录制中心-决策记录-ADR.md)

已定版决策的事实源，主要约束：

- Web 使用平台启动的受控浏览器；
- 移动端通过远程画面操作；
- 定位器必须有快照证据；
- 正式测试仍走现有 v2 Runner；
- ADR-11～16 已定版：宿主机 Recorder Agent、有头 Web Recorder、上下文保留、移动端 Native Network 降级、主/补充会话和 Web AI 安全探索边界均有事实源；
- ADR-09 已由 Web XHR/Fetch 事件与离线 Mock 实现覆盖，接口断言仍复用现有步骤封装。

## 二、早期元素库来源

### [AI 功能用例 → UI 自动化用例 M8 实施文档](./ai_ui_automation_m8_plan.md)

这是项目最早明确提出“元素库 / Page Object”路线的文档，核心模型是：

```text
页面
→ 元素语义名称
→ 真实 selector
→ AI 将业务动作映射到元素
```

它将 `page_objects` 元素库定位为 M8.2 长期能力，并提出把 Playwright DOM 扫描得到的高频元素逐步沉淀为项目元素库。

### [AI 功能用例与元素库生成接口自动化用例——实施规划](./AI功能用例与元素库生成接口自动化用例-实施规划.md)

2026-08-13 新增的接口自动化规划，定义两条来源路径：

- 功能用例提供业务意图，元素库绑定的页面、动作和 XHR/Fetch 提供接口事实；
- 没有功能用例时，从主录制基线的页面流程和网络请求图生成接口 smoke 与证据支持的边界场景。

规划复用现有 `AI_FEATURE_API_CASE_GEN`、`api_case_contract`、`AiRun` 草稿历史和 v2 Runner，不允许仅凭元素名字虚构 method/path。

## 三、本次新增评审稿

### 1. [元素库与录制浮窗 MVP 需求与技术方案](./元素库与录制浮窗-MVP需求与技术方案.md)

根据 2026-08-06 新需求编写的专项文档，重点覆盖：

- 元素库入口放在 Web / Android / iOS“新建用例”之后；
- 可拖动浮窗和独立浏览器窗口；
- 录制、暂停、停止；
- 点击元素后展示 CSS、XPath、ID 等定位器。

该文档是 2026-08-06 的专项评审输入，其入口、浮窗、弹出窗口和定位器需求已合并进主需求 v1.4。后续开发以主需求和 ADR 为准，本文件不单独作为开发基线。

### 2. [UI 录制中心页面样式 v1.4](./UI录制中心-页面样式-v1.4.html)

当前交互样式基线，可离线直接打开，包含：

- Web / Android / iOS 用例页入口示意；
- 项目元素库抽屉；
- Jam 式可拖拽紧凑悬浮录制条；
- 暂停/继续、元素拾取、独立窗口和停止交互；
- 停止后的步骤时间线、画面、定位器、Console、Network、用户事件与环境结果页。

文件为单一 HTML，不依赖网络资源；复制到其他电脑后也可以直接打开。

### 3. [元素库页面样式初稿](./元素库页面样式-初稿.html)

本次生成的可交互页面样式，包含：

- 用例页入口；
- 元素库抽屉；
- 大型录制浮窗；
- 独立录制窗口。

这版已被明确否定，保留只用于记录讨论过程。主要问题是过于接近传统管理后台和大型 Inspector，没有贴近 Jam 的紧凑悬浮录制器交互。

## 四、原型资产

### [UI 录制中心旧版线框图](./assets/ui-recording-center-wireframe.svg)

旧线框图采用“左侧会话、中间实时画面、右侧元素树、底部动作时间线”的完整工作台结构，更接近 Appium Inspector，而不是 Jam 式紧凑浮层。

## 五、参考产品

- [Jam 官网](https://jam.dev/)
- [Jam：Creating a Jam](https://jam.dev/docs/creating-a-jam)
- [Jam：Video Screen Recording](https://jam.dev/docs/video)
- [Playwright Codegen](https://playwright.dev/docs/codegen)
- [Appium Inspector](https://appium.github.io/appium-inspector/latest/)

## 六、后续整理原则

v1.4 已按以下组合完成重新设计：

```text
Jam 式紧凑悬浮录制条
+ 旧文档中的页面/元素事实库
+ 点击元素后展开的定位器侧栏
+ 停止后打开的独立元素整理页面
```

当前基线、ADR 与代码实现已同步。功能验收操作见 [`UI录制中心-验收清单.md`](./UI录制中心-验收清单.md)。后续只需在宿主机 Xcode/XCUITest 环境可用后补做 iOS Simulator 真实端到端验收；复杂 WebSocket/SSE、跨域 iframe、Canvas/WebGL 等仍按首期能力边界处理。
