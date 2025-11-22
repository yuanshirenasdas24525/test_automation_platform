
# Test Automation Platform

## 1. 项目概览

本项目是基于 **Appium + Python** 的移动自动化框架，支持 **Android/iOS/Web**。  
经过重构后，项目模块清晰，职责分离，便于维护和扩展。

## 2. 目录结构
```
src/core/mobile/
├── app_action.py                 # 核心调度器，执行步骤顺序
├── finder/
│   └── finder.py                 # 元素查找、滑动查找、黑名单处理
├── actions/
│   ├── executor.py               # 执行动作统一入口，支持 ActionRegistry
│   ├── registry.py               # 动作注册表
│   └── value_resolver.py         # 处理 step value 替换、function:xxx 调用
├── assertions/
│   └── assertion.py              # 断言逻辑
├── cache/
│   └── parameter_cache.py        # 参数缓存管理
├── device/
│   ├── device_action.py          # 设备控制总入口
│   ├── input_controller.py       # 输入类操作（tap, swipe, back, keyevent）
│   ├── system_controller.py      # 系统操作（截图, 通知栏, TouchID/FaceID 等）
│   └── app_controller.py         # App 操作（启动/关闭/安装/卸载）
```
## 3. 核心模块说明

### 3.1 AppAction
AppAction 是核心调度器：
1. 调用 Finder 查找元素
2. 通过 ValueResolver 解析参数
3. 调用 ActionExecutor 执行动作
4. 可选缓存结果
5. 可选断言结果

示例：
```python
from app_action import AppAction

app = AppAction(driver)
step = {
    "action": "click",
    "by": "id",
    "finder": "login_button",
}
app.app_steps(step)
```

### 3.2 Finder
- 提供 find、swipe_find 和黑名单处理
- 自动处理元素未找到和弹窗干扰

### 3.3 ActionExecutor & ActionRegistry
- 所有动作通过 ActionRegistry 注册
- 内置基础动作：click, send_keys, clear, text, back
- 扩展动作包括：ac_send, h5_code, get_attribute, finger_print, use_touch_id, use_face_id
- start/stop screen recording, capture_logs, simulate_network_condition
- app install/uninstall/launch/close, reset, orientation, volume, device_time 等

示例调用：
```python
step = {
    "action": "ac_send",
    "by": "id",
    "finder": "username_input",
    "value": "test_user"
}
app.app_steps(step)
```

### 3.4 AssertionEngine
- 支持 assert_result(actual, expected)
- 支持数据库查询结果断言

### 3.5 ParameterCache & ValueResolver
- 支持参数缓存（缓存上一步结果）
- 支持动态参数替换 ${var}
- 支持 function:xxx 执行自定义函数

### 3.6 DeviceAction
- Facade 模式，统一调度 Input/System/App 控制器
- 支持 swipe/tap/back, TouchID/FaceID, Clipboard, Notification
- Screen Recording, Performance/Network, App install/uninstall/launch/reset
- Physical Button / Volume / Orientation 等

## 4. JSON 用例示例
```json
[
  {
    "name": "登录流程",
    "steps": [
      {
        "action": "ac_send",
        "by": "id",
        "finder": "username_input",
        "value": "test_user"
      },
      {
        "action": "ac_send",
        "by": "id",
        "finder": "password_input",
        "value": "123456"
      },
      {
        "action": "click",
        "by": "id",
        "finder": "login_button"
      },
      {
        "by": "id",
        "finder": "com.wallet.uu:id/verify",
        "action": "clear",
        "value": null,
        "deposit": null,
        "retrieve": null,
        "expected": "sql:SELECT content FROM `forex`.`t_notice_message_record` WHERE `receiver` = '${phone}' ORDER BY `create_time` DESC LIMIT 1;",,
        "sliding_location": null,
        "wait": null
      },
      {
        "by": "id",
        "finder": "com.wallet.uu:id/verify",
        "action": "send_keys",
        "value": "function:extract_code",
        "deposit": null,
        "retrieve": "sql:SELECT content FROM `forex`.`t_notice_message_record` WHERE `receiver` = '${phone}' ORDER BY `create_time` DESC LIMIT 1;",
        "expected": null,
        "sliding_location": null,
        "wait": null
      }
    ]
  }
]
```

## 5. 扩展说明
- 新增动作只需 ActionRegistry.register("action_name", func)
- func(executor, element, value) 格式
- 支持全局参数缓存、黑名单自动处理、失败截图、断言

## 6. 使用建议
1. 每个 step 以 JSON 描述
2. AppAction.app_steps(step) 执行单步
3. 可将 JSON 测试用例列表循环执行
4. 新增动作和控件可在现有模块中扩展，无需改动调度器
