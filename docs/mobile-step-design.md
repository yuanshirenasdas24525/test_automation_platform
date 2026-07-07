# 移动端步骤设计

## 平台边界

Android 与 iOS 共用 `app_*` runner，但前端编辑器按平台展示字段。

Android 用例：
- 使用 `appPackage`
- 可选 `appActivity`
- 默认 automationName：`UiAutomator2`
- 可用 Android 专属定位方式，如 `android_uiautomator`

iOS 用例：
- 使用 `bundleId`
- 默认 automationName：`XCUITest`
- 可用 iOS 专属定位方式，如 `ios_predicate`、`ios_class_chain`

## 公共能力

以下步骤保留公共用法：
- 安装 App
- 截图
- 点击
- 输入
- 滑动
- 等待
- 断言文本
- 通用动作
- 通用断言
- HTTP 请求
- sleep

## 平台专属差异

`app_launch`：
- Android 显示 `appPackage`、`appActivity`
- iOS 显示 `bundleId`
- 两端都保留 `automationName`、`noReset`

`app_uninstall` / `app_activate` / `app_terminate`：
- Android 显示 `appPackage`
- iOS 显示 `bundleId`

`app_press`：
- 仅 Android 显示。

`app_hide_keyboard`：
- iOS 显示 `key_name`
- Android 不需要额外字段。

## 保存前校验

保存 Android/iOS 用例时会校验：
- Android `app_launch` 必须有 `appPackage`
- iOS `app_launch` 必须有 `bundleId`
- 卸载/激活/杀进程按平台要求 `appPackage` 或 `bundleId`
- iOS 不允许保存 `app_press`

## 后续优化

- 增加 Android/iOS 专属用例模板。
- 增加步骤级“平台徽标”。
- 对历史脏数据提供一键清理：删除当前平台不适用字段。
