你是一名资深接口测试工程师。请基于下面的「接口摘要」，为「{{MODULE_NAME}}」模块**本批指定的测试点**生成详细的**接口测试用例**。

# 接口摘要（digest）
{{DIGEST}}

# 当前项目其它模块（用于交叉考量）
{{CROSS_MODULE_CONTEXT}}

# 本批要生成的测试点（只为这些点生成，不要自行扩展别的点）
{{BATCH_POINTS}}

# 已经生成过 / 已存在的用例名（务必不要与这些重复）
{{DONE_NAMES}}

# 硬性要求

1. **只覆盖上面列出的本批测试点**，一个测试点通常对应 1 条用例。
2. **操作步骤要写清一次完整的接口请求**，让人照着就能发：
   - 请求方法与路径，如 `发送 POST /api/login`
   - 请求头，如 `Header: Content-Type: application/json`、`Header: Authorization: Bearer <有效token>`（鉴权用例写明 token 状态）
   - 请求参数 / Body，如 `Body: {"username": "admin", "password": "Test#123"}`（给具体值；异常用例给触发异常的具体值，如缺字段、错类型、超长）
   - 有依赖的先写前置请求，如 `先调用 POST /api/login 获取 token`
3. **预期结果具体、可验证**，按条列出：
   - 响应状态码，如 `状态码 200`
   - 关键响应字段，如 `响应体含 token 字段且非空`、`role = admin`
   - 异常用例写明错误码/错误信息，如 `状态码 400`、`msg = "password 不能为空"`
4. **前置条件**：列出必要前置（如「已存在 admin 账号」「服务已启动」「已获取有效 token」），无则空数组。
5. **连贯性**：method/path/字段名与接口摘要、已生成用例保持一致；**绝不重复**「已生成/已存在用例名」；有依赖的接口在前置或步骤里写明。
6. `steps` / `expected` / `preconditions` 都是字符串数组，每个元素一条，**不要自己加序号前缀**（前端会自动编号）。

# 输出格式（严格 JSON，只输出一个 ```json``` 代码块，不要任何额外文字）

```json
[
  {
    "name": "POST /api/login 合法账号密码登录成功返回 token",
    "preconditions": ["已存在 admin 账号，密码 Test#123", "服务已启动"],
    "steps": [
      "发送 POST /api/login",
      "Header: Content-Type: application/json",
      "Body: {\"username\": \"admin\", \"password\": \"Test#123\"}"
    ],
    "expected": [
      "状态码 200",
      "响应体含 token 字段且非空",
      "响应体 role = admin"
    ]
  }
]
```

约束：整个返回值是能被 `json.loads` 解析的合法 JSON 数组；不要在数组外写注释或解释。
