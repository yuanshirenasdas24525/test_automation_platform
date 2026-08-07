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

M1 已完成首个可验收的 Web 录制切片：

- 宿主机 Recorder Agent 能启动受控 Chromium / Firefox / WebKit 浏览器；
- 录制、暂停、继续、停止由服务端真实控制，不再只是前端状态切换；
- 自动采集点击、输入、提交、滚动、URL、Console、页面异常和 XHR/Fetch 元数据；
- 敏感输入和请求头自动脱敏，网络正文受容量限制；
- 点击或输入后生成截图，并提取 ID、CSS、name、text、link、XPath 候选定位器；
- 事件增量写入数据库，同时归并为项目元素和主定位器；
- 元素库工作区实时展示事件时间线、元素、定位器和 Agent 连接状态。

本切片尚未交付 M2 的资源归档、请求 Mock 和业务流程离线回放，也未交付 M3 的 Android/iOS 模拟器录制。悬浮条自由拖动和独立窗口将在 M1 后续切片补齐。

## 8. 验证策略

- Python：模型/Schema 导入和 `compileall`；
- API：状态机合法/非法转换、批量事件顺序与项目授权；
- 数据库：Alembic upgrade/downgrade 脚本人工 review；
- TypeScript：`npm run typecheck`、`npm run build`、`npm run lint`；
- UI：Web/Android/iOS 入口、平台筛选、空状态、创建会话和控制按钮；
- Recorder：真实 Web 页面端到端采集、事件入库与定位器归并；
- 后续：模拟器端到端采集与 Web 离线断网业务流程回放。
