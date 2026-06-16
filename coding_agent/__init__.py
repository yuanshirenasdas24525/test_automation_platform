"""coding_agent —— AI Studio M1：AI 下发编码模块。

职责拆分：
- ``git_ops``：clone / branch / commit / push，凭证从 Project 解密
- ``diff/``  ：unified diff 解析 / apply / dry-run
- ``rag/``   ：代码索引 / embedding / 检索（第 3 批实施）
- ``prompt_templates``：对话 / finalize / 编码 prompt（第 4-5 批实施）

复用现有 ``ai_gateway`` 做底层 LLM 调用（chat_json / chat_markdown），本模块
只负责 prompt 组装、RAG 检索、diff 处理、git 落地，不引入新的 provider 抽象。

注意：本模块绝不直接 import SQLAlchemy session / FastAPI 对象 —— 所有外部依赖
通过函数参数注入，方便将来从平台抽离单跑。
"""
