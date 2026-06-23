你是一名资深接口测试工程师。下面是一份测试报告里**多条接口用例**的执行结果。请对**每一条**用例做全面分析，找出问题并分类。

# 用例与执行结果（数组，每项含 case_id、name、用例定义 def、执行结果 result）
{{CASES}}

# 对每条用例，结合**真实响应**全面分析以下方面

1. **提取参数**：需要提取的值有没有提取到？`extract` 的 JSONPath 对不对——**响应里明明有该值但 `extract_values` 是 null，就是 JSONPath 路径写错了**（如响应 `{"data":{"token":"x"}}`、extract 写 `$.token` 取不到，应改 `$.data.token`）。哪些后续会用到的值（token、id）该提取却没提取？
2. **断言**：断言是否足够？哪些关键响应字段 / 状态需要补充断言？
3. **SQL 断言**：写/改/删库类接口是否需要补一条 `sql:SELECT...` 查库断言？
4. **请求参数**：参数有没有写错（导致接口报错或语义不对）？
5. **动态值**：哪些参数需要用 `function:xxx` 动态生成（如随机手机号、时间戳、唯一订单号），不能写死？
6. **分类 classification**（四选一）：
   - **用例问题**：接口其实正常响应了，但用例写错了（JSONPath 错、断言错、参数错）。**给出修正 fix（extract/assertion）**。
   - **接口问题**：真实响应确实返回错误/缺字段/数据不符业务（先确认响应里真没有该数据）。
   - **环境/其他**：连不上 / 超时 / 5xx / 鉴权未配 / token 失效 / 依赖或前置数据缺失。
   - **正常**：本次其实通过、无问题。

# 输出（严格 JSON 数组，按输入顺序，每条用例一个对象，只输出一个 ```json``` 块）

```json
[
  {
    "case_id": 123,
    "name": "合法账号密码登录成功",
    "classification": "用例问题",
    "findings": [
      "提取 token 失败：响应里 token 在 data.token，extract 却写成 $.token，应改为 $.data.token",
      "建议补充断言：$.data.user.id 非空",
      "建议补充 SQL 断言：SELECT count(*) FROM users WHERE username='admin'"
    ],
    "fix": { "extract": {"token": "$.data.token"}, "assertion": {"status_code": 200, "$.status": "success"} }
  }
]
```

约束：
- `case_id` 用输入里给的；`findings` 是字符串数组、写具体（引用真实响应）。
- `fix` 只在 classification=**用例问题** 时给；接口问题/环境/正常 一律给 `{}`。
- **不要清空/破坏已经正确的部分**：`extract_values` 显示已成功提取（非 null）说明 extract 路径对，**保持原样、绝不清成 `{}`**；只修真正写错的路径。`fix.assertion` 同理，保留对的、只补/改错的。
- **动态值（token/id/时间戳等每次都变的）断言用 `"not_empty"`**（系统转成"非空"断言），不要断言它等于某具体值。
- 只输出 JSON 数组。
