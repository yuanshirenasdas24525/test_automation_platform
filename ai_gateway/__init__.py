"""AI Gateway —— 暂未实现。

目标：封装 LiteLLM，对 platform 层提供统一的 /chat /embedding /analyze_failure
等端点，支持多 provider（OpenAI / Anthropic / 本地模型）切换。

暂时是一个空包，便于未来独立成微服务或沿着 platform.services.ai_proxy 继续演进。
预期文件：
  - main.py            独立 FastAPI 入口（uvicorn ai_gateway.main:app）
  - llm/               LiteLLM 包装、provider 配置
  - prompts/           prompt 模板集中管理
  - services/          业务编排：文档→用例、报告→失败分析、Swagger→用例
  - api/               REST 端点

对外契约（Week 8-9 落地）：
  POST /v1/chat                 通用对话
  POST /v1/generate_cases       从 Swagger/文档生成用例
  POST /v1/analyze_failure      对一条失败 step 做根因推断
"""
