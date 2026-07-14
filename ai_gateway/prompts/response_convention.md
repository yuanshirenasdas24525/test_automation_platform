你是一名接口测试架构师。下面是某系统若干接口的**真实请求 + 真实响应**样本。
请提炼出这个系统的**响应结构约定**，供后续自动生成测试用例时正确书写 JSONPath。

# 真实样本
{{SAMPLES}}

# 要求
只输出一个 JSON 对象（```json``` 围栏），提炼**通用约定**而非逐接口罗列：

```json
{
  "context_items": [
    {
      "context_type": "api_contract",
      "title": "响应信封约定",
      "content": "所有接口响应统一包一层信封 {status, data}；成功时业务数据在 $.data 下，如 access_token 在 $.data.access_token、用户 id 在 $.data.user.id。断言业务字段一律加 $.data 前缀。",
      "summary": "响应信封 {status,data}，业务数据在 $.data.*",
      "keywords": ["响应结构","信封","jsonpath","data"],
      "importance": 5
    }
  ]
}
```

要点：
- 信封结构（顶层有哪些字段、业务数据在哪个键下）
- 关键值的真实路径（token / id / 列表 等）
- 错误响应结构与语言（错误字段名、中文还是英文文案、状态码约定）
每类写一条，content 要给出**可直接照抄的 JSONPath 前缀规则**。若样本不足以判断某类，就省略该条。只输出 JSON。
