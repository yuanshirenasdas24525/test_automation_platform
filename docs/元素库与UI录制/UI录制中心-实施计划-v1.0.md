# UI 录制中心实施计划 v1.0

> 状态：实施中
> 基线日期：2026-08-07
> 需求基线：[`UI录制中心-需求文档与实施方案.md`](./UI录制中心-需求文档与实施方案.md)
> 页面基线：可视化元素库采用“左侧页面导航 + 中间可交互页面/模拟器 + 右侧定位器”的工作区形态。

## 1. 已确认的产品边界

1. Web 离线能力选择“第三层：业务流程可以离线操作”：保存页面资源、DOM、路由状态及脱敏后的 XHR/Fetch 请求响应，通过本地 Mock 运行常规业务流程。
2. Android/iOS 首期只支持 Android Emulator 和 iOS Simulator，暂不支持真机。
3. 断言继续复用现有步骤编辑器与 Runner 的封装，本期不建设智能断言生成。
4. 录制采用“结构化事件流为主，画面/视频为辅”；录制结果必须能生成现有 `TestStep`，不得新增正式执行入口。
5. 元素按“项目 → 平台 → 页面 → 页面版本”组织，不以跨页面平铺列表作为主要浏览方式。

## 2. 首期目标

形成以下最小闭环：

```text
Web/Android/iOS 用例页
→ 元素库
→ 创建录制会话
→ 录制/暂停/继续/停止
→ 采集动作与技术上下文
→ 页面快照、元素及多定位器入库
→ 可视化页面中查看和维护
→ 后续生成现有 TestStep 草稿
```

首期不改变 `TestCase → TestStep → StepDispatcher → Runner` 执行链路。

## 3. 架构拆分

### 3.1 平台控制面

FastAPI 与 PostgreSQL 负责：

- 录制会话状态机；
- 事件批量接收、顺序和幂等约束；
- 页面快照、元素、定位器和离线 Mock 交换记录；
- 项目级权限校验；
- 前端查询、审核和控制接口。

### 3.2 Recorder Agent

Recorder Agent 在宿主机原生运行，负责持有长生命周期的 Playwright/Appium Session：

- Web：Playwright 有头浏览器、DOM、Console、XHR/Fetch、截图与用户事件；
- Android：Emulator + Appium + UIAutomator Tree；
- iOS：Simulator + Appium/XCUITest + Accessibility Tree；
- 敏感信息在 Agent 侧先脱敏，再批量上报；
- Agent 通过会话 API 上报事件、快照和元素事实。

Celery 仅处理快照规范化、指纹、去重、离线包构建等短任务，不持有浏览器或 Appium Session。

### 3.3 离线业务回放

Web 离线包包含：

- HTML、CSS、JS、图片和字体资源清单；
- DOM/ARIA 快照和页面路由；
- XHR/Fetch 的脱敏请求键、响应与匹配规则；
- 页面状态、元素热区和定位器证据；
- 本地 Mock Service Worker/服务器配置。

首期支持登录、列表、搜索、筛选、分页、详情和普通表单等常见流程。WebSocket、SSE、支付、第三方登录、跨域 iframe、复杂 Canvas/WebGL 明确标记为能力受限。

移动端的“离线”通过模拟器场景恢复实现，保存应用版本、模拟器配置、初始化脚本/数据、UI Tree、操作事件和 Mock 配置；没有可用模拟器时只读查看，不能伪装成可交互状态。

## 4. 数据域

第一批表：

| 表 | 责任 |
|---|---|
| `ui_recording_sessions` | 项目、平台、状态、环境、设备、能力与生命周期 |
| `ui_recording_events` | 用户动作、Console、Network、导航、日志等统一事件流 |
| `ui_page_snapshots` | 页面/场景版本、画面、DOM/UI Tree、资源清单和环境证据 |
| `ui_elements` | 项目级元素事实，绑定平台和页面 |
| `ui_element_locators` | 一个元素的多个定位器、评分和验证状态 |
| `ui_mock_exchanges` | 离线业务回放所需的脱敏 XHR/Fetch 请求响应与匹配规则 |

数据库只保存结构化索引和小体积元数据；截图、视频、DOM/UI Tree 和离线资源包通过存储抽象保存 URI。

## 5. API 契约

首批控制面接口：

```text
POST /api/ui-recordings
GET  /api/ui-recordings
GET  /api/ui-recordings/{id}
POST /api/ui-recordings/{id}/start
POST /api/ui-recordings/{id}/pause
POST /api/ui-recordings/{id}/resume
POST /api/ui-recordings/{id}/stop
POST /api/ui-recordings/{id}/cancel
GET  /api/ui-recordings/{id}/events
POST /api/ui-recordings/{id}/events:batch
GET  /api/ui-elements
GET  /api/ui-elements/{id}
```

所有按 ID 访问的资源都必须从资源反查 `project_id` 并调用统一项目授权守卫。

## 6. 里程碑

### M0：控制面与页面入口

- 数据模型与 Alembic 迁移；
- 会话状态机和事件批量接收 API；
- Web/Android/iOS 用例页“元素库”入口；
- 可视化元素库工作区；
- 录制/暂停/继续/停止控制与会话恢复。

验收：三个 UI 用例页均能创建独立项目会话，状态转换合法，刷新后可恢复，会话和事件不能跨项目访问。

### M1：Web Recorder 与技术上下文

- 宿主机 Recorder Agent；
- Playwright 用户事件、DOM、截图、Console、pageerror、XHR/Fetch 和环境采集；
- 事件批量上报、脱敏、容量限制和失败补传；
- 页面快照、元素和候选定位器入库；
- 悬浮录制条和独立窗口。

验收：常规 React/Vue 页面操作识别率不低于 95%，Console/XHR/Fetch 元数据覆盖率不低于 98%。

### M2：Web 离线业务回放

- 资源归档和 URL 重写；
- XHR/Fetch 请求匹配与本地 Mock；
- 多响应序列和状态切换；
- 离线包完整性检查、版本管理和能力限制报告。

验收：登录、列表、筛选、分页、详情和表单流程在断开原服务后可完成回放；未命中的请求必须显式报错，不得悄悄访问线上。

### M3：Android/iOS 模拟器

- Android Emulator 与 iOS Simulator 预检；
- Appium 远程画面操作、UI Tree、截图和设备日志；
- 移动端元素定位器；
- 应用版本和场景恢复；
- Native Network 不可用时显式展示降级原因。

验收：各一台模拟器完成录制、暂停、恢复、停止、元素入库和场景重开。

### M4：用例草稿与执行上下文复用

- 录制动作转换为现有 `TestStep` 草稿；
- 复用现有断言编辑器；
- 正式执行旁路接入同一上下文事件协议；
- Allure/报告页关联上下文结果。

验收：录制草稿经人工确认后能走现有 v2 链路执行；Collector 降级不改变 Runner 原始结果。

## 7. 当前实施切片

M0 控制面已经交付：数据模型、迁移、会话控制 API、事件接收契约、项目级元素查询接口，以及用例页入口和可视化工作区均已落地。

M1 已完成：

- 宿主机 Recorder Agent 能启动受控 Chromium / Firefox / WebKit 浏览器；
- 录制、暂停、继续、停止由服务端真实控制，不再只是前端状态切换；
- 自动采集点击、输入、提交、滚动、URL、Console、页面异常和 XHR/Fetch 元数据；
- 敏感输入和请求头自动脱敏，网络正文受容量限制；
- 点击或输入后生成截图，并提取 ID、CSS、name、text、link、XPath 候选定位器；
- 事件增量写入数据库，同时归并为项目元素和主定位器；
- 元素库工作区实时展示事件时间线、元素、定位器和 Agent 连接状态。
- 录制控制改为 488px 紧凑悬浮条，可在当前视口内自由拖动和收起；
- 主窗口与独立窗口通过 8 秒短租约同步控制权，命令使用幂等键防止重复执行；
- 拾取模式会在捕获阶段阻止真实业务点击，仅采集元素、截图和定位器；
- 停止录制前二次确认，暂停期间停止普通事件、Console、Network 和画面采集。

M2 已完成首个可验收切片：

- 录制期间归档已访问页面的 HTML、JS、CSS、字体和图片，单会话默认上限 100MB；
- 页面导航和停止时保存 DOM、视口截图与页面版本，并与项目元素按 `page_key` 联合展示；
- XHR/Fetch 请求与响应配对写入 `ui_mock_exchanges`，保留多响应顺序；
- 停止时生成带页面、资源、Mock、容量和限制说明的 `manifest.json`；
- 页面文档、截图和静态资源记录 SHA-256，回放启动前逐文件校验，损坏包拒绝打开；
- 离线浏览器通过 Playwright Route 强制拦截所有请求：命中本地页面/资源/Mock，未命中返回 599，不访问原服务；
- 元素库可从已完成会话打开离线回放，并在真实页面截图上叠加可点击元素热区；
- 已实测原页面服务完全关闭时页面加载成功、内联交互脚本运行、Fetch 命中 Mock，`misses=0`。

M3 已完成首个可验收切片：

- Recorder Agent 提供 Appium、UiAutomator2/XCUITest 驱动和已启动模拟器预检；
- 移动录制必须绑定显式标识的模拟器，开始时占用设备、停止/取消时释放；
- Android/iOS 共用 Appium Session，采集模拟器截图、UI Tree、Activity/Context、设备/应用/视口环境；
- 元素库工作区直接显示真实模拟器截图，点击、拖动、返回、刷新和文本输入均经平台转发；
- 非破坏性拾取可从坐标命中的 UI Tree 节点生成 Android ID/Accessibility ID/UiAutomator/XPath，或 iOS Accessibility ID/Predicate/Class Chain/XPath；
- 停止时尽力采集 logcat/syslog，过滤 Appium 协议噪声；Native Network 未配置代理/SDK 时明确标记降级；
- 完成会话保存同一模拟器与应用版本绑定，可通过“重开场景”继续补录，并明确暂不恢复应用私有数据。

真实验证结果：Android Emulator `emulator-5554` 已完成启动、画面/UI Tree、非破坏性拾取、真实点击、暂停、继续、停止和 logcat 闭环。iOS 代码路径和定位器单测已通过，但当前宿主机的 XCUITest Driver 目录由 root 持有且 Xcode/WebDriverAgent 无法匹配现有 Simulator runtime，预检会显示未就绪；修复宿主机驱动权限/版本后再做 iOS 真机式端到端验收。

M2 仍需继续增强复杂请求匹配规则、跨域 iframe、文件上传、WebSocket/流式响应的能力报告，以及登录、列表、筛选、分页、详情、表单的完整业务样本验收。M3 仍需补应用私有数据/初始化脚本还原、WebView 混合树与 Native Network 代理/SDK。

M4 已完成首个用例草稿切片：

- 已完成录制可生成现有 v2 Runner 的 `TestStep` 草稿；
- Web 自动映射 `web_goto/web_click/web_input/web_select`，移动端映射 `app_tap/app_input/app_swipe/app_back`；
- 定位器按平台可执行白名单和录制评分选主定位器，非破坏性 `user.pick` 不会误生成动作步骤；
- 密码值保持 `${password}` 脱敏变量并提示人工配置；无对应 Runner 的滚动、提交、刷新事件保留在技术上下文并展示警告；
- 用户确认用例名称、模块和步骤后保存到现有用例库，断言继续在原步骤编辑器维护。

M4 仍需把同一上下文事件协议接入正式 v2 执行与 Allure/报告页，并支持草稿内逐步删除、改定位器等更细的人工调整。

## 8. 验证策略

- Python：模型/Schema 导入和 `compileall`；
- API：状态机合法/非法转换、批量事件顺序与项目授权；
- 数据库：Alembic upgrade/downgrade 脚本人工 review；
- TypeScript：`npm run typecheck`、`npm run build`、`npm run lint`；
- UI：Web/Android/iOS 入口、平台筛选、空状态、创建会话、悬浮控制条、独立窗口和截图热区；
- Recorder：真实 Web 页面端到端采集、事件入库与定位器归并；
- 离线：停止原页面服务后启动回放，验证页面/资源/Mock 命中和未命中请求阻断；
- 后续：完整业务样本断网验收、iOS 宿主机修复后的端到端采集，以及移动场景数据还原。
