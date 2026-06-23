你是一名资深接口测试工程师。下面是一批接口用例**真实试跑**后拿到的「请求 + 真实响应」。请根据**每条用例的真实响应结构**，重新生成正确的「提取参数 extract」和「断言 assertion」，纠正之前凭空猜测的 JSONPath。

# 试跑样本（每条含 用例名 name、请求、真实响应 response、HTTP 状态码 status）
{{SAMPLES}}

# 要求

1. **严格按真实响应结构写 JSONPath**：
   - `extract`：对象 {变量名: JSONPath}。只提取后续用例可能要用的值（如 token、新建数据的 id）。JSONPath 必须能在该条的真实 response 里取到值；用 `$.` 开头，按真实层级写（如响应是 `{"code":0,"data":{"token":"..."}}` 就写 `{"token": "$.data.token"}`）。该用例响应里没有可提取的就写 {}。
   - `assertion`：对象 {JSONPath 或 status_code: 期望值}。**每条都要有断言**。至少断言 HTTP 状态码（`{"status_code": <真实status>}`）；正常用例再断言业务码/关键字段（按真实响应里的实际字段名与值，如 `{"status_code": 200, "$.code": 0}`）；异常用例按真实错误响应断言（如 `{"status_code": 422, "$.code": 422}`）。
2. **以真实响应为准**：响应里实际是什么结构就按什么写，不要再用想象的 `$.data.xxx`。如果某条 status 是错误码或 response 是报错文本，就按它实际的来断言。
3. 试跑失败（response 里是 error / 拿不到）的用例：`extract` 给 {}，`assertion` 只给能确定的（如有 status 就断言 status_code），并在 `note` 里简述原因。

# 输出格式（严格 JSON，只输出一个 ```json``` 代码块，按输入顺序一一对应）

```json
[
  {
    "name": "合法账号登录成功",
    "extract": {"token": "$.data.token"},
    "assertion": {"status_code": 200, "$.code": 0},
    "note": ""
  }
]
```

约束：`name` 必须与输入样本的 name 完全一致；返回数组每项含 `name`/`extract`/`assertion`，可选 `note`。只输出 JSON。
