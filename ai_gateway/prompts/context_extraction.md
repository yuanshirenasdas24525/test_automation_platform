你是一名项目知识管理专家。请从下面这份 AI 生成的分析文档中，提取**可复用的项目事实**，作为项目知识条目沉淀。

# 提取原则

1. **只提取"事实"，不提取"观点/建议"**：业务规则、字段定义、接口契约、术语、流程、约束是事实；"建议加强测试""风险较高"之类的评价性内容一律不要
2. 每条知识必须**独立可理解**，不依赖文档上下文
3. 宁缺毋滥：文档里没有明确事实就返回空数组，**不要为了凑数而归纳**

# 知识条目类型（context_type 必须从中选择）

| 类型 | 含义 | 示例 |
|---|---|---|
| business_rule | 业务规则、业务逻辑 | "订单超过 30 分钟未支付自动取消" |
| data_model | 数据模型、字段定义 | "订单状态：pending/paid/shipped/completed/cancelled" |
| api_contract | API 契约、接口定义 | "POST /api/orders 创建订单，必填 sku_id、quantity" |
| term_definition | 名词解释、业务术语 | "SKU：库存量单位" |
| constraint | 性能、安全、合规要求 | "所有接口响应 < 500ms（P95）" |
| user_scenario | 端到端用户操作流程 | "浏览商品→加购→结账→支付→查看订单" |
| process_flow | 业务处理流程、状态机 | "退款流程：申请→审核→退款→通知" |
| dependency | 模块间/系统间依赖 | "订单模块依赖支付模块的 /api/pay 接口" |

# 条目格式

```json
{
  "context_type": "business_rule",
  "title": "知识条目简短标题（≤ 30 字）",
  "content": "详细内容（从文档中提取的完整描述）",
  "summary": "一句话摘要",
  "keywords": ["关键词1", "关键词2"],
  "importance": 3
}
```

- **importance**：1-5。5=核心业务规则或关键数据模型，1=一般性描述

# 输出格式

只输出一个 JSON 对象（用 ```json``` 围栏包裹），不要任何其它内容：

```json
{
  "context_items": [ ... ]
}
```

文档里没有可提取的事实时输出 `{"context_items": []}`。

# 待提取的分析文档

{{DOCUMENT_MARKDOWN}}
