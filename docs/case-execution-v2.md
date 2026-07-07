# 自动化用例执行链路 v2

## 唯一入口

所有自动化用例统一走：

```text
POST /api/run_test
  -> tasks/run_test_task.py
  -> pytest tests/service_run_executor.py::TestService::test_case_runner
  -> runners/case_executor.py
  -> runners/dispatcher.py
  -> runners/steps/*
```

支持的自动化类型：
- `api`
- `web`
- `android`
- `ios`
- `mixed`

`functional` 是人工功能用例，不走自动化执行链路。

## 前端工作台

自动化用例统一使用：

```text
frontend/src/pages/AutomationCasesPage.tsx
```

它替代了早期只面向 API 的 `ApiCasesPage`。后端接口路径仍沿用 `/api/api_cases`，但前端主命名已改为 `automationCasesApi`。

## 步骤编辑

步骤编辑器：

```text
frontend/src/components/case/step-editor.tsx
```

按 `case_type` 限制可用步骤：
- API 多步骤：`http_request`、`assert`、`sleep`
- Web：`web_*` + 通用步骤
- Android/iOS：`app_*` + 通用步骤
- Mixed：保留 Web/App/通用步骤

## 不变量

- Runner 不直接抛异常，统一返回 `StepResult`。
- 重试、等待、失败处理在 dispatcher 层统一处理。
- 没有 steps 的老用例应先跑数据迁移，不再恢复 v1 runner。
- Android/iOS 运行前需要选择设备。

## 当前维护重点

- 保持 `case_type` 查询隔离，避免 API/Web/App 串数据。
- 自动化页面命名使用 `Automation*`，只有后端老路径保留 `api_cases`。
- 移动端平台字段保存前必须校验，避免 iOS 用例保存 Android 字段或反过来。
