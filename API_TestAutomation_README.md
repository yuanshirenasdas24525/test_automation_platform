# API 自动化测试模块

## 1. 📘 项目概览
本模块是一个 高度通用、可复用、可扩展 的 API 自动化测试框架，用于支持：	
-	📚 数据驱动（Excel / YAML / JSON）
-	📦 多环境管理（host 动态切换）
-	🔧 统一 request 构建
-	📁 文件上传
-	🔒 参数加密
-	🔑 动态参数替换 ${var} 与 function:xxx 执行自定义函数
-	🚀 接口请求发送
-	✅ 可扩展的断言体系与数据库查询结果断言
-	🔗 可与 Web UI / App UI 自动化共用

该模块已实现 **低耦合设计**，可适配任何项目并可作为企业级自动化测试框架的基础模块。

## 2.  📂 目录结构
```
core/api/
├── api_client.py              # 请求发送
├── factory.py                 # 创建统一 API 组件
├── file_parameter.py          # 文件参数化
├── request_data_processor.py  # url/header/参数/加密/文件 数据处理
core/utils/
├── encrypt.py                 # 加密方法(单独文件)
├── allure_utils.py            # allure 报告工具
├── function_executor.py       # 自定义函数工具
├── logger.py                  # 日志工具
├── platform_utils.py          # 平台工具
├── read_test_cases.py         # 读取测试用例工具
├── redis_utils.py             # redis 工具
├── sql_handler.py             # sql 工具
tests/
test_api.py                    # 执行测试用例入口                  
```

## 3. 🏗 模块架构总览
API 自动化模块由 3 个核心组件构成：
```aiignore
┌────────────────────────┐
│      ApiClient         │  ← 请求发送
└─────────▲──────────────┘
          │
┌─────────┴──────────────┐
│  RequestDataProcessor  │  ← 请求构建器（url/header/参数/加密/文件）
└─────────▲──────────────┘
          │
┌─────────┴──────────────┐
│       Factory          │  ← 创建统一 API 组件
└─────────▲──────────────┘
          │
┌─────────┴──────────────┐
│       TestApi          │  ← 通过pytest统一管理用例
└─────────▲──────────────┘
          │
┌─────────┴──────────────┐
│    GenericCaseReader   │  ← 读取测试用例（支持excel/csv/json/YAML）
└────────────────────────┘
```
文件上传由独立模块 FileParameter 负责，保证可维护性和稳定性。

## 4. 🧩 模块说明
1. GenericCaseReader
2. TestApi
3. Factory
4. RequestDataProcessor
5. ApiClient

## 5. 使用示例

```python
from src.core.api.factory import create_api_client

# 创建 API 客户端
client = create_api_client()

# 调用接口
response = client.send_case(
    ["case_module", "case_submodule", "case_name", "case_title", "skip", "post", "/api/login", "{header}",
     "application/json", "{data}", "1image.png;2image.png", "{extra}", "SELECT * FROM user", "{expect}", "2"]
)

print(response)
```

## 6. 📌 支持的数据驱动（示例）
excel
|case_module|case_submodule|case_name|case_title|skip|method|path|header|parametric_type|data|file_path|extra|sql|expect|wait|
|-----------|--------------|---------|----------|----|------|----|------|---------------|----|---------|-----|----|------|----|
|api        |login         |login    |登录测试   | Y   |post  |/api/login|{header}|application/json|{data}|1image.png;2image.png|{extra}|SELECT * FROM user|{expect}|2|
```json
{
  "case_module": "case_module",
  "case_submodule": "case_submodule",
  "case_name": "case_name",
  "case_title": "case_title",
  "skip": "Y/N",
  "method": "post",
  "path": "/api/login",
  "header": {"token":"${token}"},
  "parametric_type": "application/json",
  "data": {
    "username": "function:generate_account",
    "password": "test_password",
    "uid": "${uid}"
  },
  "file_path": "/path/to/file",
  "extra": {
    "verifyToken":"$.data[0].token",
    "my_email":"function:generate_email_d"
  },
  "sql": "SELECT * FROM user WHERE email = '${my_email}'",
  "expect": {
    "$.success": true,
    "$.data.cryptoList[?(@.coinSymbol == 'USDT')].totalBalance": "function:assert_amount_deduction"
  },
  "wait": "2.0"
}
```

## 7. 🔧 扩展说明
1. 自定义函数执行
2. 加密方法

## 8. 🧱 设计理念
本模块适合作为企业级测试基础设施的一部分，并可与 Web UI / App UI 自动化统一成一套完整测试平台。




