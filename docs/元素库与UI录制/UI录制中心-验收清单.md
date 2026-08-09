# UI 录制中心验收清单

> 更新日期：2026-08-09
> 适用范围：元素库、Web 离线业务回放、Android/iOS 模拟器、用例草稿和正式执行技术上下文

## 1. 验收前启动

本次包含数据库迁移、Recorder Agent 和 Celery 执行链路变更，验收前需要完整重启：

```bash
./stop-dev.sh
./start-dev.sh
```

`start-dev.sh` 会执行数据库初始化/迁移，并启动 API、Recorder Agent、Celery worker/beat 和 Vite。确认以下地址可用：

- 平台：`http://localhost:5173`
- API：`http://127.0.0.1:54351`
- Recorder Agent：`http://127.0.0.1:54352/health`

## 2. Web 录制与元素库

1. 进入项目的 Web 用例页，确认“元素库”位于“新建用例”后面。
2. 打开元素库，创建 Web 录制并输入目标地址。
3. 确认悬浮条可拖动、可弹出独立窗口，并可执行录制、暂停、继续、拾取和停止。
4. 在目标页面执行输入、点击、弹窗打开/关闭和路由跳转。
5. 停止后确认页面导航使用路由/项目/版本等可区分名称，而不是全部显示同一个站点标题。
6. 选择动作，确认步骤前后画面、点击目标高亮、Console、Network、用户事件和环境信息同时可见。
7. 修改元素语义名称/别名，增删定位器、设主定位器并执行验证；刷新后数据仍保留。
8. 新建 Web 用例，在点击或输入步骤中选择“从元素库选择”，确认自动回填元素和定位器。
9. 分别打开“删除元素”“删除当前页面”和“删除录制记录”，确认都有不可恢复提示和二次确认；运行中录制的删除按钮应禁用，删除完成后可通过 `/api/ui-deletion-audits?project_id=<ID>` 查询操作人及级联范围。

## 3. 严格离线业务流程

1. 选择已完成且显示“离线可用”的录制会话，点击“离线交互”。
2. 在离线页面输入任意测试值并点击按钮，确认点击、输入、弹窗和路由跳转有效。
3. 在左侧选择元素，确认中间页面定位并高亮；点击中间页面元素，确认右侧反选并展示定位器。
4. 对登录/列表/筛选/分页/详情/普通表单执行已录制流程，确认 XHR/Fetch 命中本地 Mock。
5. 未归档或未命中的请求必须显式失败，不能访问原服务。

## 4. Android/iOS 模拟器

1. 先启动 Android Emulator 或 iOS Simulator，再在对应平台页创建录制。
2. 确认平台只列出模拟器，不把真机列为可用设备。
3. 在远程画面执行点击、拖动、返回、刷新和文本输入；拾取模式不能触发真实业务点击。
4. 选择元素后确认 Android 定位器包含 ID/Accessibility ID/UiAutomator/XPath，iOS 包含 Accessibility ID/Predicate/Class Chain/XPath（以节点实际属性为准）。
5. 停止后点击“重开场景”：Android 恢复 Emulator Snapshot，iOS 恢复应用数据容器。
6. Native Network 未配置代理/SDK 时应看到明确降级说明。

iOS 当前仍要求宿主机先具备可启动且与 Appium XCUITest Driver 匹配的 Simulator Runtime/WebDriverAgent。预检未通过时属于宿主环境问题，平台应显示未就绪，不能假成功。

## 5. 用例草稿与正式执行上下文

1. 在录制结果中修改动作名称、调整顺序、忽略无关动作，然后生成用例草稿。
2. 确认草稿使用现有 `web_*`/`app_*` 步骤并可保存到原用例库；密码只保留 `${password}` 变量。
3. 执行生成的用例，确认仍走现有 v2 Runner，不出现新的执行入口。
4. 在执行记录/报告页点击“技术上下文”，按步骤检查：
   - 步骤前后截图；
   - Console/Runner 日志；
   - XHR/Fetch 或 HTTP exchange；
   - URL、浏览器、系统、视口、网络能力或降级原因；
   - Android/iOS 设备能力（移动用例）。
5. 检查上下文中不出现原始 Cookie、Bearer Token、密码、Access Token 或 Refresh Token。

## 6. 首期能力边界

- WebSocket、SSE/流式响应、支付、第三方登录、跨域 iframe、复杂 Canvas/WebGL 和浏览器文件选择器不保证离线回放。
- Android/iOS 首期只支持模拟器，不支持真机。
- 移动 Native Network 依赖额外代理或应用 SDK。
- 断言继续复用现有步骤编辑器与 Runner 封装。

这些场景必须显示限制或未命中错误，不能静默回源或伪装为已采集。
