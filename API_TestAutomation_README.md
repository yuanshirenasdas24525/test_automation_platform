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

   针对excel/csv/json/YAML大部分文件类型进行统一的读写，在用特定的方法，对用例进行整理，针对不同项目，生成不同格式的数据。

2. TestApi

   在这里调用读取测试用例，创建实例，统一下发请求

3. Factory

   加载配置，创建RequestDataProcessor实例，ApiClient实例

4. RequestDataProcessor

   处理数据(请求头，请求参数，提取参数)，断言，执行sql，执行功能函数

5. ApiClient

   发送http请求，重试机制，session管理，测试报告编号处理

## 5.  🚀 使用示例

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
|登录        |登录         |正常登录    |账号密码正确，请求登录   | Y/N |post  |/api/login|{"token":"${token}"}|application/json|{"username": "function:generate_account",    "password": "test_password", "uid": "${uid}"}|1image.png;/path/to/image.png|{"verifyToken":"$.data[0].token",    "my_email":"function:generate_email_d" }|SELECT * FROM user|{"$.success": true,    "$.data.cryptoList[?(@.coinSymbol == 'USDT')].totalBalance": "function:assert_amount_deduction"  }|2|
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

   在request_data_processor.py中引入了``` exec_func``` 模块，用于执行参数中一些函数方法，可在请求头，请求参数，或者断言中去执行特定的函数方法。

   直接去```src/utils/function_executor```文件中的```function_name```方法中添加或编辑方法，在用例中以```function:function_name```引用，参数放在```extra```传递，参数可以是sql语句。

   后续函数参数可以单独优化，开一个字段单独传递函数参数，目前放在```extra```字段中不影响。

   ```
   from src.utils.function_executor import exec_func
   
   def _process_functions(self, data: dict, sql: str, extra_str: str):
       """
       处理字典中以 function: 开头的值，执行对应函数。
       自动传入 sql 查询结果（可能是多个）、当前变量和参数池。
       """
       sql_results = self.execute_select_fetchone(sql, extra_str)  # 返回 list
       def _process(item: dict, parent_data):
           if isinstance(item, dict):
               for k, v in item.items():
                   item[k] = _process(v, item)  # 递归，并传当前 dict 给子节点
               return item
           elif isinstance(item, list):
               for i, v in enumerate(item):
                   item[i] = _process(v, item)  # 递归，并传当前 list 给子节点
               return item
           elif isinstance(item, str) and item.startswith("function:"):
               return exec_func(item, sql_results, parent_data, self.extra_pool)
           return item
   
       return _process(data, data)
   ```

   示例1：在请求头中执行

   ```python
   # 请求参数
   {
     "token":"${token}".
     "abc":"function:generate_account"
   }
   
   def handler_header(self, header_str: str, data: str, sql: str) -> dict:
       """
       处理请求头，合并base_header和传入header，支持加密处理。
       """
       if not header_str:
           header_str = '{}'
       headers = {**self.base_header, **self.handler_data(header_str, sql)}
       # 请求头参数加密
       if self.encryption_decryption.get('on_off'):
           variable = self.handler_data(data, sql)
           from src.utils.encrypt import ParameterEncryption
           pe = ParameterEncryption(data=variable, 		               power_access_key=self.encryption_decryption['key'])
           headers.update(pe.ed_header())
       return headers
   ```

   示例2：在请求体中获取谷歌动态验证码

   ```python
   # 请求参数
   {
       "username": "function:generate_account",
       "password": "test_password",
       "uid": "${uid}"
   }
   
   def handler_data(self, variable: str, sql: str, extra_str: str = None) -> Any:
       """
       处理请求数据，替换表达式并执行函数。
       """
       if not variable:
           return {}
   
       try:
           variable = rep_expr(variable, self.extra_pool)
           data_obj = convert_json(variable)
       except Exception:
           return {}
   
       if isinstance(data_obj, dict):
           self._process_functions(data_obj, sql, extra_str)
       return data_obj
   ```

   示例3：在extra中执行，加入到参数池中去

   ```python
   # 正常参数
   {"verifyToken":"$.data[0].token"}
   # 执行函数参数
   {"my_email":"function:generate_email_d"}
   
   def handler_extra(self, extra_str: str, response: dict) -> None:
       """
       从响应中提取参数，加入 extra_pool
       """
       if not extra_str:
           return
       extra_dict = convert_json(extra_str)
       for k, v in extra_dict.items():
           if isinstance(v, str) and v.startswith("function:"):
               self.extra_pool[k] = exec_func(v)
           extracted_value = extractor(response, v)
           if extracted_value is not None:
               self.extra_pool[k] = extracted_value
   ```

   示例4：在断言中执行(动态金额断言)

   ```python
   # 请求参数
   {
       "$.success": true,
       "$.data.cryptoList[?(@.coinSymbol == 'USDT')].totalBalance": "function:assert_amount_deduction"
   }
   
       def assert_result(self, response: dict, expect_str: str) -> None:
           """
           断言响应与预期是否一致
           """
           function_amount_assert = ["function:assert_amount_increase", "function:assert_amount_deduction"]
           add_allure_step("当前可用参数池", self.extra_pool)
           expect_str = rep_expr(expect_str, self.extra_pool)
           expect_dict = convert_json(expect_str)
           for k, v in expect_dict.items():
               actual = extractor(response, k)
               if isinstance(v, str) and v.startswith("function:"):
                   if v in function_amount_assert:
                       v = float(exec_func(v, self.extra_pool))
                   else:
                       v = exec_func(v)
               assert actual == v, f"断言失败: 实际值 {actual} != 预期值 {v}"
               add_allure_step("断言", f"实际值：{actual} == 预期值：{v}")
   ```

2. 加密方法

   可以使用```function:function_name```方式对单独加密参数进行加解密

   ```python
   def captcha_solver(*args, **kwargs):
       token = solve_captcha()
       return token
   ```

   也可以对参数整体进行加解密

   ```python
   def handler_header(self, header_str: str, data: str, sql: str) -> dict:
       """
       处理请求头，合并base_header和传入header，支持加密处理。
       """
       if not header_str:
           header_str = '{}'
       headers = {**self.base_header, **self.handler_data(header_str, sql)}
       # 请求头参数加密
       if self.encryption_decryption.get('on_off'):
           variable = self.handler_data(data, sql)
           from src.utils.encrypt import ParameterEncryption
           pe = ParameterEncryption(data=variable, 		               power_access_key=self.encryption_decryption['key'])
           headers.update(pe.ed_header())
       return headers
   ```

   

## 8. 🧱 设计理念
本模块适合作为企业级测试基础设施的一部分，并可与 Web UI / App UI 自动化统一成一套完整测试平台。




